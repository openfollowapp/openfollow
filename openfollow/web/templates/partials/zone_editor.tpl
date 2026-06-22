% from openfollow.web.routes import osc_destinations_script_json
% _osc_dests_json = osc_destinations_script_json(config)
<div id="zone-editor-section" class="section" data-fold-key="zone_editor" data-help="zone_editor" data-template-form="1">
    <div class="section-head">
        <h2>Zone Editor</h2>
        <span class="section-note">Click to place vertices. Click the first vertex or double-click to close. Click a zone to select it. Drag vertices to move them.</span>
    </div>

    <div class="group">
        <div id="zone-canvas-wrap" style="position:relative;border:1px solid var(--border,#444);border-radius:6px;background:#111;overflow:hidden;width:100%;">
            <canvas id="zone-canvas" width="1280" height="720" style="display:block;width:100%;height:auto;background:#111;cursor:crosshair;"></canvas>
        </div>
        <div style="display:flex;gap:0.5rem;margin-top:0.5rem;flex-wrap:wrap;">
            <button type="button" id="zone-add-btn" class="save-btn">+ New Zone</button>
            <button type="button" id="zone-finish-btn" class="secondary" disabled>Finish Polygon</button>
            <button type="button" id="zone-cancel-btn" class="secondary" disabled>Cancel Drawing</button>
            <button type="button" id="zone-delete-btn" class="danger" disabled>Delete Selected</button>
            <!-- Save / Load template buttons. Save captures entire
                 trigger_zones section (defaults + zones[]) as
                 .openfollowtemplate under templates/user/. Load lists
                 all zones templates (system + user), confirms, then
                 replaces section (API requires ?confirm=1). -->
            <button type="button" id="zone-template-save-btn" class="secondary"
                    data-template-save
                    data-template-deps="#zone-editor-section, #trigger-zones-section">Save as template…</button>
            <button type="button" id="zone-template-load-btn" class="secondary">Load template…</button>
            <span id="zone-editor-status" class="section-note"></span>
        </div>
        <div id="zone-details" class="section-note" style="margin-top:1rem;">Select a zone to edit its OSC addresses and trigger source.</div>
    </div>
</div>

<script>
(function() {
    if (window.__zoneEditorInit) return;
    window.__zoneEditorInit = true;

    // Shared OSC destinations a zone can target (id + label + endpoint).
    // Seeded server-side for the first render, then refreshed from the
    // /api/zones poll so add/rename/delete in the OSC Destinations section
    // reaches the dropdown without a full page reload.
    var OSC_DESTINATIONS = {{!_osc_dests_json}};

    // Zone color palette seeded by base.tpl via window.OPENFOLLOW_PALETTE
    // (from openfollow.palette). pickZoneColor delegates to shared
    // window.OpenFollow.nextUnusedColor helper.

    var canvas = document.getElementById('zone-canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var statusEl = document.getElementById('zone-editor-status');
    var detailsEl = document.getElementById('zone-details');
    var addBtn = document.getElementById('zone-add-btn');
    var finishBtn = document.getElementById('zone-finish-btn');
    var cancelBtn = document.getElementById('zone-cancel-btn');
    var deleteBtn = document.getElementById('zone-delete-btn');

    // #154: vertex display/edit goes through the shared unit helpers
    // (window.OpenFollow.units, /assets/js/units.js, seeded from
    // config.ui.unit_system in base.tpl). ``z.vertices`` and the /api/zones
    // save path stay in METRES – conversion is display-boundary only.
    var OFU = window.OpenFollow.units;
    var IS_IMPERIAL = OFU.isImperial();
    function formatLength(m) { return OFU.formatLength(m); }
    function parseLength(r) { return OFU.parseLength(r); }
    function metricEcho(m) { return OFU.metricEcho(m); }
    function vertexUnitLabel() { return OFU.unitSuffixLength(); }

    var state = {
        zones: [],
        markers: [],
        grid: {width: 10, depth: 10, spacing: 1, x_offset: 0, y_offset: 0},
        selectedIndex: -1,
        detailsRenderedIndex: -2,  // sentinel: never rendered
        draft: null,  // {vertices: [[x,y],...]} while drawing
        dragging: null,  // {zoneIndex, vertexIndex}
    };

    function fetchZones() {
        fetch('/api/zones', {cache: 'no-store'})
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                // Don't replace the selected zone's data while the user is
                // typing into its detail inputs (would clobber in-progress
                // edits – in particular per-vertex coordinate fields).
                var activeEl = document.activeElement;
                var editingSelected = activeEl && detailsEl.contains(activeEl) && state.selectedIndex >= 0;
                var incomingZones = data.zones || [];
                if (editingSelected && state.zones[state.selectedIndex]) {
                    var preserved = state.zones[state.selectedIndex];
                    state.zones = incomingZones;
                    if (state.selectedIndex < state.zones.length) {
                        state.zones[state.selectedIndex] = preserved;
                    }
                } else {
                    state.zones = incomingZones;
                }
                state.markers = data.markers || [];
                if (data.grid) state.grid = data.grid;
                // Refresh the shared destinations so the OSC Destination
                // dropdown follows add/rename/delete live.
                var newDests = data.destinations || [];
                var destsChanged = JSON.stringify(newDests) !== JSON.stringify(OSC_DESTINATIONS);
                OSC_DESTINATIONS = newDests;
                if (state.selectedIndex >= state.zones.length) {
                    state.selectedIndex = -1;
                }
                render();
                // Only rebuild the details panel when the selection actually
                // changes; otherwise we'd clobber open dropdowns / color
                // pickers / focused inputs on every poll tick. Destinations
                // changing is the one extra reason to rebuild, but only when
                // the operator isn't mid-edit on this zone's details.
                if (state.selectedIndex !== state.detailsRenderedIndex) {
                    renderDetails();
                } else if (destsChanged && state.selectedIndex >= 0 && !editingSelected) {
                    renderDetails();
                } else if (state.selectedIndex >= 0) {
                    // Refresh Diagnostics tab in place (don't rebuild whole
                    // panel). Rendering fresh occupants/last_event_* into
                    // existing #zone-diag-* spans leaves focus untouched.
                    var current = data.zones[state.selectedIndex];
                    if (current) renderZoneDiagnostics(current);
                }
            })
            .catch(function() {});
    }

    // Visible world extent: large enough to fit the grid AND every
    // zone vertex with one grid-spacing unit of padding around the
    // furthest point. Returns ``[fullWidth, fullDepth]`` in world
    // units, both symmetric around ``grid.x_offset`` / ``grid.y_offset``
    // so the grid stays visually centred even when zones extend
    // asymmetrically.
    //
    // While the operator is dragging a vertex, the extent is FROZEN
    // to the value captured at drag-start. Continuous recomputation
    // would zoom the canvas in/out as the pointer moves across the
    // edge, which both feels jarring AND moves the pointer's world
    // coordinate underneath the operator's cursor mid-drag. On
    // drag release ``_frozenExtent`` is cleared and the next render
    // re-fits the canvas to the new geometry.
    function computeViewExtent() {
        if (state._frozenExtent) {
            return state._frozenExtent;
        }
        return _computeViewExtentLive();
    }

    function _computeViewExtentLive() {
        var spacing = Math.max(0.1, state.grid.spacing);
        var hw = state.grid.width / 2;
        var hd = state.grid.depth / 2;
        // Start from the grid corners – the viewport never shrinks
        // below the grid even when all zones are inside it.
        var minX = state.grid.x_offset - hw;
        var maxX = state.grid.x_offset + hw;
        var minY = state.grid.y_offset - hd;
        var maxY = state.grid.y_offset + hd;
        // Expand the bbox to include every committed zone vertex.
        if (state.zones && state.zones.length) {
            for (var i = 0; i < state.zones.length; i++) {
                var verts = (state.zones[i] && state.zones[i].vertices) || [];
                for (var j = 0; j < verts.length; j++) {
                    var v = verts[j];
                    if (v[0] < minX) minX = v[0];
                    if (v[0] > maxX) maxX = v[0];
                    if (v[1] < minY) minY = v[1];
                    if (v[1] > maxY) maxY = v[1];
                }
            }
        }
        // Pad one grid-spacing unit on each side so the furthest
        // vertex never sits on the canvas edge.
        minX -= spacing;
        maxX += spacing;
        minY -= spacing;
        maxY += spacing;
        // Symmetric half-extent around the grid centre – keeps the
        // grid centred even when zones extend asymmetrically.
        var halfW = Math.max(
            state.grid.x_offset - minX,
            maxX - state.grid.x_offset,
        );
        var halfD = Math.max(
            state.grid.y_offset - minY,
            maxY - state.grid.y_offset,
        );
        return [Math.max(0.5, halfW * 2), Math.max(0.5, halfD * 2)];
    }

    // Zone vertices and marker positions are in PSN-absolute coords
    // (PSN origin = 0,0). The grid rectangle is visually centred on canvas
    // and is positioned in PSN space via grid.x_offset / grid.y_offset.
    function worldToScreen(wx, wy) {
        var pad = 20;
        var W = canvas.width - pad * 2;
        var H = canvas.height - pad * 2;
        var ext = computeViewExtent();
        var scale = Math.min(W / ext[0], H / ext[1]);
        var cx = canvas.width / 2;
        var cy = canvas.height / 2;
        // y axis: PSN +Y = upstage; draw upstage toward top of canvas
        var sx = cx + (wx - state.grid.x_offset) * scale;
        var sy = cy - (wy - state.grid.y_offset) * scale;
        return [sx, sy];
    }

    function screenToWorld(sx, sy) {
        var pad = 20;
        var W = canvas.width - pad * 2;
        var H = canvas.height - pad * 2;
        var ext = computeViewExtent();
        var scale = Math.min(W / ext[0], H / ext[1]);
        var cx = canvas.width / 2;
        var cy = canvas.height / 2;
        var wx = (sx - cx) / scale + state.grid.x_offset;
        var wy = -(sy - cy) / scale + state.grid.y_offset;
        return [wx, wy];
    }

    function mousePos(evt) {
        var rect = canvas.getBoundingClientRect();
        var scaleX = canvas.width / rect.width;
        var scaleY = canvas.height / rect.height;
        return [
            (evt.clientX - rect.left) * scaleX,
            (evt.clientY - rect.top) * scaleY,
        ];
    }

    function renderGrid() {
        ctx.fillStyle = '#111';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        var spacing = Math.max(0.1, state.grid.spacing);
        var hw = state.grid.width / 2;
        var hd = state.grid.depth / 2;

        // Grid lines
        ctx.strokeStyle = 'rgba(120,120,120,0.25)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (var gx = -hw; gx <= hw + 1e-6; gx += spacing) {
            var a = worldToScreen(state.grid.x_offset + gx, state.grid.y_offset - hd);
            var b = worldToScreen(state.grid.x_offset + gx, state.grid.y_offset + hd);
            ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
        }
        for (var gy = -hd; gy <= hd + 1e-6; gy += spacing) {
            var a2 = worldToScreen(state.grid.x_offset - hw, state.grid.y_offset + gy);
            var b2 = worldToScreen(state.grid.x_offset + hw, state.grid.y_offset + gy);
            ctx.moveTo(a2[0], a2[1]); ctx.lineTo(b2[0], b2[1]);
        }
        ctx.stroke();

        // Grid outline
        ctx.strokeStyle = 'rgba(200,200,200,0.6)';
        ctx.lineWidth = 1.5;
        var c1 = worldToScreen(state.grid.x_offset - hw, state.grid.y_offset - hd);
        var c2 = worldToScreen(state.grid.x_offset + hw, state.grid.y_offset - hd);
        var c3 = worldToScreen(state.grid.x_offset + hw, state.grid.y_offset + hd);
        var c4 = worldToScreen(state.grid.x_offset - hw, state.grid.y_offset + hd);
        ctx.beginPath();
        ctx.moveTo(c1[0], c1[1]);
        ctx.lineTo(c2[0], c2[1]);
        ctx.lineTo(c3[0], c3[1]);
        ctx.lineTo(c4[0], c4[1]);
        ctx.closePath();
        ctx.stroke();

        // PSN origin marker
        var o = worldToScreen(0, 0);
        ctx.fillStyle = 'rgba(200,200,200,0.5)';
        ctx.beginPath();
        ctx.arc(o[0], o[1], 3, 0, Math.PI * 2);
        ctx.fill();
    }

    function renderZones() {
        for (var i = 0; i < state.zones.length; i++) {
            var z = state.zones[i];
            if (!z.vertices || z.vertices.length < 2) continue;
            var isSel = (i === state.selectedIndex);
            var isOcc = !!z.is_occupied;
            ctx.beginPath();
            for (var j = 0; j < z.vertices.length; j++) {
                var v = z.vertices[j];
                var p = worldToScreen(v[0], v[1]);
                if (j === 0) ctx.moveTo(p[0], p[1]);
                else ctx.lineTo(p[0], p[1]);
            }
            if (z.vertices.length >= 3) ctx.closePath();

            var col = z.color || '#ff8000';
            ctx.fillStyle = hexToRgba(col, isOcc ? 0.38 : 0.18);
            if (z.vertices.length >= 3) ctx.fill();
            ctx.strokeStyle = hexToRgba(col, isSel ? 1.0 : 0.8);
            ctx.lineWidth = isSel ? 2.5 : (isOcc ? 2.0 : 1.25);
            ctx.stroke();

            // Vertices (only for selected)
            if (isSel) {
                for (var k = 0; k < z.vertices.length; k++) {
                    var vv = z.vertices[k];
                    var pp = worldToScreen(vv[0], vv[1]);
                    ctx.fillStyle = '#fff';
                    ctx.strokeStyle = col;
                    ctx.lineWidth = 1.5;
                    ctx.beginPath();
                    ctx.arc(pp[0], pp[1], 5, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.stroke();
                    // Index label – matches the ``V<n>`` rows in the
                    // VERTICES list of the details panel so operators
                    // can cross-reference which canvas vertex maps to
                    // which row when editing coords numerically. Black
                    // outline keeps the white digits legible against
                    // any zone fill colour.
                    var idxText = 'V' + (k + 1);
                    var lx = pp[0] + 8;
                    var ly = pp[1] - 8;
                    ctx.font = 'bold 11px sans-serif';
                    ctx.textAlign = 'start';
                    ctx.textBaseline = 'alphabetic';
                    ctx.lineWidth = 3;
                    ctx.strokeStyle = 'rgba(0,0,0,0.85)';
                    ctx.strokeText(idxText, lx, ly);
                    ctx.fillStyle = '#fff';
                    ctx.fillText(idxText, lx, ly);
                }
            }

            // Label
            if (z.name) {
                var cx = 0, cy = 0;
                for (var m = 0; m < z.vertices.length; m++) {
                    var pv = worldToScreen(z.vertices[m][0], z.vertices[m][1]);
                    cx += pv[0]; cy += pv[1];
                }
                cx /= z.vertices.length; cy /= z.vertices.length;
                ctx.font = '12px sans-serif';
                var txt = isOcc ? (z.name + ' (' + z.occupant_count + ')') : z.name;
                var metrics = ctx.measureText(txt);
                ctx.fillStyle = 'rgba(0,0,0,0.6)';
                ctx.fillRect(cx - metrics.width/2 - 4, cy - 9, metrics.width + 8, 16);
                ctx.fillStyle = col;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(txt, cx, cy);
                ctx.textAlign = 'start';
                ctx.textBaseline = 'alphabetic';
            }
        }
    }

    function renderDraft() {
        if (!state.draft) return;
        var verts = state.draft.vertices;
        if (verts.length === 0) return;
        ctx.strokeStyle = '#7cf';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        for (var i = 0; i < verts.length; i++) {
            var p = worldToScreen(verts[i][0], verts[i][1]);
            if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        for (var j = 0; j < verts.length; j++) {
            var pp = worldToScreen(verts[j][0], verts[j][1]);
            ctx.fillStyle = (j === 0) ? '#ffd76a' : '#7cf';
            ctx.beginPath();
            ctx.arc(pp[0], pp[1], 6, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    function renderMarkers() {
        for (var i = 0; i < state.markers.length; i++) {
            var m = state.markers[i];
            var p = worldToScreen(m.x, m.y);
            ctx.fillStyle = '#5cf';
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(p[0], p[1], 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = '#cfe8ff';
            ctx.font = '10px sans-serif';
            ctx.fillText('#' + m.id, p[0] + 7, p[1] - 6);
        }
    }

    function render() {
        renderGrid();
        renderZones();
        renderDraft();
        renderMarkers();
        updateButtons();
    }

    function updateButtons() {
        var drawing = !!state.draft;
        finishBtn.disabled = !(drawing && state.draft.vertices.length >= 3);
        cancelBtn.disabled = !drawing;
        deleteBtn.disabled = (state.selectedIndex < 0);
        addBtn.disabled = drawing;
        if (drawing) {
            statusEl.textContent = 'Drawing: ' + state.draft.vertices.length + ' point(s). Click first vertex or double-click to close.';
        } else if (state.selectedIndex >= 0) {
            statusEl.textContent = 'Editing zone ' + (state.selectedIndex + 1);
        } else {
            statusEl.textContent = '';
        }
        // Drawing-in-progress is a form of unsaved state: vertices
        // are on the canvas but not yet on disk. Mark the section
        // dirty so "Save as template" disables until the operator
        // either Finishes or Cancels – otherwise the template would
        // capture the on-disk zones list and silently drop the
        // in-flight polygon.
        var section = document.getElementById('zone-editor-section');
        if (section) {
            if (drawing) window.markFormDirty && window.markFormDirty(section);
            // Don't auto-clean on draw end – the per-zone edit form's
            // own input listener may have set dirty; let the explicit
            // ``Save Zone`` flow clean it.
        }
    }

    function hexToRgba(hex, a) {
        var h = (hex || '').replace('#', '');
        if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
        if (h.length !== 6) return 'rgba(255,128,0,' + a + ')';
        var r = parseInt(h.slice(0,2), 16);
        var g = parseInt(h.slice(2,4), 16);
        var b = parseInt(h.slice(4,6), 16);
        return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
    }

    function pointInPoly(x, y, verts) {
        var inside = false;
        for (var i = 0, j = verts.length - 1; i < verts.length; j = i++) {
            var xi = verts[i][0], yi = verts[i][1];
            var xj = verts[j][0], yj = verts[j][1];
            var intersect = ((yi > y) !== (yj > y)) &&
                (x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-9) + xi);
            if (intersect) inside = !inside;
        }
        return inside;
    }

    function findVertexAt(sx, sy) {
        if (state.selectedIndex < 0) return null;
        var z = state.zones[state.selectedIndex];
        if (!z || !z.vertices) return null;
        for (var i = 0; i < z.vertices.length; i++) {
            var p = worldToScreen(z.vertices[i][0], z.vertices[i][1]);
            var dx = p[0] - sx, dy = p[1] - sy;
            if (dx * dx + dy * dy <= 49) {
                return i;
            }
        }
        return null;
    }

    function findZoneAt(wx, wy) {
        for (var i = state.zones.length - 1; i >= 0; i--) {
            var z = state.zones[i];
            if (z.vertices && z.vertices.length >= 3 && pointInPoly(wx, wy, z.vertices)) {
                return i;
            }
        }
        return -1;
    }

    // Tabbed zone-detail panel (Basic / Area / Settings / Diagnostics).
    // Reuses .row-tab-btn/.row-tab-panel CSS and global switchRowTab
    // handler with data-row-tabs-scope fallback (JS-rendered, no form).
    function renderDetails() {
        state.detailsRenderedIndex = state.selectedIndex;
        if (state.selectedIndex < 0) {
            detailsEl.innerHTML = '<div class="section-note">Select a zone to edit its OSC addresses and trigger source.</div>';
            return;
        }
        var z = state.zones[state.selectedIndex];
        if (!z) return;
        var idx = state.selectedIndex;
        var html = '';
        // ``data-row-tabs-scope`` is the opt-in marker the global
        // ``switchRowTab`` looks for as its third fallback (see
        // ``base.tpl``); without it the click handler can't find a
        // scope to swap panels in.
        html += '<div class="group" data-row-tabs-scope style="margin:0;">';
        html += '  <h3 class="group-title">Zone ' + (idx + 1) + ' Details</h3>';
        html += '  <div class="row-tab-bar" role="tablist">';
        html += '    <button type="button" class="row-tab-btn active" data-row-tab="zone-basic" role="tab" aria-selected="true" aria-controls="row-tab-zone-basic">Basic</button>';
        html += '    <button type="button" class="row-tab-btn" data-row-tab="zone-area" role="tab" aria-selected="false" aria-controls="row-tab-zone-area">Area</button>';
        html += '    <button type="button" class="row-tab-btn" data-row-tab="zone-settings" role="tab" aria-selected="false" aria-controls="row-tab-zone-settings">Settings</button>';
        html += '    <button type="button" class="row-tab-btn" data-row-tab="zone-diag" role="tab" aria-selected="false" aria-controls="row-tab-zone-diag">Diagnostics</button>';
        html += '  </div>';

        // ---- Basic tab: identity + trigger source + per-marker filter ----
        html += '  <div class="row-tab-panel active" id="row-tab-zone-basic" role="tabpanel">';
        html += '    <div class="row">';
        html += '      <div class="field"><label>Name</label><input type="text" data-zone-field="name" value="' + escapeAttr(z.name) + '"></div>';
        // Native picker replaced by circle-swatch full-variant.
        // Hidden data-zone-field input is what collectDetailFields reads;
        // picker's onChange keeps the two in sync.
        html += '      <div class="field"><label>Color</label>' +
            '<button type="button" class="color-swatch-trigger" data-color-picker="full" ' +
            'data-value="' + escapeAttr(z.color || '#ff8000') + '" aria-label="Zone colour"></button>' +
            '<input type="hidden" data-zone-field="color" value="' + escapeAttr(z.color || '#ff8000') + '">' +
            '</div>';
        html += '      <div class="field checkbox-field"><label>Enabled</label><div class="checkbox-wrap"><input type="checkbox" data-zone-field="enabled" ' + (z.enabled ? 'checked' : '') + '></div></div>';
        html += '    </div>';
        html += '    <div class="row">';
        html += '      <div class="field"><label>Trigger Source</label><select data-zone-field="trigger_source">';
        html += '        <option value="markers" ' + (z.trigger_source === 'markers' ? 'selected' : '') + '>Markers (PSN)</option>';
        html += '        <option value="detection" ' + (z.trigger_source === 'detection' ? 'selected' : '') + '>Detection (AI)</option>';
        html += '        <option value="both" ' + (z.trigger_source === 'both' ? 'selected' : '') + '>Both</option>';
        html += '      </select></div>';
        html += '      <div class="field" style="flex:2 1 0;">';
        html += '        <label>Triggered By <span class="section-note" style="font-weight:normal;">(marker IDs only – empty = any)</span></label>';
        html += '        <input type="text" data-zone-field="triggered_by"'
              + ' data-zone-validate-field="triggered_by"'
              + ' value="' + escapeAttr((z.triggered_by || []).join(', ')) + '"'
              + ' placeholder="e.g. 0, 1, 5"'
              + ' aria-describedby="zone-triggered-by-error">';
        html += '        <span class="field-error" id="zone-triggered-by-error" data-zone-field-error="triggered_by" aria-live="polite"></span>';
        html += '      </div>';
        html += '    </div>';
        html += '  </div>';

        // ---- Area tab: vertex coordinate list (canvas stays at top) ----
        html += '  <div class="row-tab-panel" id="row-tab-zone-area" role="tabpanel">';
        html += '    <div class="row"><div class="field" style="flex:1 1 100%;">';
        html += '      <label>Vertices (x, y in ' + vertexUnitLabel() + ')</label>';
        html += '      <div id="zone-vertex-list" style="display:flex;flex-direction:column;gap:0.25rem;">';
        // Imperial: free-text ft/in inputs (parsed on edit) + a Stored echo of
        // the canonical metres. Metric: the original number inputs, unchanged.
        var vType = IS_IMPERIAL ? 'text' : 'number';
        var vStep = IS_IMPERIAL ? '' : ' step="0.01"';
        var vWidth = IS_IMPERIAL ? '9em' : '7em';
        for (var vi = 0; vi < z.vertices.length; vi++) {
            var vx = z.vertices[vi][0];
            var vy = z.vertices[vi][1];
            var canDel = z.vertices.length > 3 ? '' : 'disabled';
            // Imperial wraps the row in a column so the per-vertex "Stored:"
            // echo can sit beneath it. Metric keeps the original single-row
            // markup (no wrapper, no echo) so its DOM is unchanged.
            if (IS_IMPERIAL) {
                html += '        <div style="display:flex;flex-direction:column;gap:0.1rem;">';
            }
            html += '        <div style="display:flex;align-items:center;gap:0.25rem;">';
            html += '          <span style="min-width:2.5em;font-size:0.85em;opacity:0.7;">V' + (vi + 1) + '</span>';
            html += '          <input type="' + vType + '"' + vStep + ' data-vertex-field="x" data-vertex-index="' + vi + '" value="' + (IS_IMPERIAL ? formatLength(vx) : Number(vx).toFixed(3)) + '" style="width:' + vWidth + ';">';
            html += '          <input type="' + vType + '"' + vStep + ' data-vertex-field="y" data-vertex-index="' + vi + '" value="' + (IS_IMPERIAL ? formatLength(vy) : Number(vy).toFixed(3)) + '" style="width:' + vWidth + ';">';
            html += '          <button type="button" class="secondary" data-vertex-delete="' + vi + '" title="Remove vertex" ' + canDel + '>×</button>';
            html += '        </div>';
            if (IS_IMPERIAL) {
                html += '          <small class="metric-echo" data-vertex-echo="' + vi + '">Stored: ' + metricEcho(vx) + ', ' + metricEcho(vy) + '</small>';
                html += '        </div>';
            }
        }
        html += '      </div>';
        html += '      <span class="section-note">Click on the canvas to add vertices, drag to move them, click ×  to remove.</span>';
        html += '    </div></div>';
        html += '  </div>';

        // ---- Settings tab: OSC destination + the four addresses ----
        html += '  <div class="row-tab-panel" id="row-tab-zone-settings" role="tabpanel">';
        html += '    <div class="row">';
        html += '      <div class="field"><label>OSC Destination</label><select data-zone-field="destination_id">';
        html += '        <option value=""' + (!z.destination_id ? ' selected' : '') + '>(none – zone will not send)</option>';
        var destFound = false;
        for (var di = 0; di < OSC_DESTINATIONS.length; di++) {
            var od = OSC_DESTINATIONS[di];
            var sel = (od.id === z.destination_id) ? ' selected' : '';
            if (sel) destFound = true;
            html += '<option value="' + escapeAttr(od.id) + '"' + sel + '>' + escapeAttr(od.name || '(unnamed)') + '</option>';
        }
        // A zone pointing at a deleted destination keeps its dangling id: show
        // it as a selected (disabled) option so the dropdown reflects the
        // stored state instead of silently falling back to "(none)".
        if (z.destination_id && !destFound) {
            html += '<option value="' + escapeAttr(z.destination_id) + '" selected disabled>(missing destination)</option>';
        }
        html += '      </select></div>';
        html += '    </div>';
        html += '    <div class="row">';
        html += oscField('First Entry Address', 'osc_address_first_entry', z.osc_address_first_entry || '', '/zone/enter');
        html += oscField('Additional Entry Address', 'osc_address_additional_entry', z.osc_address_additional_entry || '', '/zone/count');
        html += '    </div>';
        html += '    <div class="row">';
        html += oscField('Partial Exit Address', 'osc_address_partial_exit', z.osc_address_partial_exit || '', '/zone/partial');
        html += oscField('Final Exit Address', 'osc_address_final_exit', z.osc_address_final_exit || '', '/zone/exit');
        html += '    </div>';
        html += '  </div>';

        // ---- Diagnostics tab: live state + Test send (mirrors the
        // OSC Output Diagnostics card layout via the shared ``diag-*``
        // classes in base.tpl). Two stacked ``.diag-card`` panels:
        //   1. Live state – occupants + last-event in a ``.diag-row``
        //      grid (same shape OSC Live status / Preview use).
        //   2. Test send – four entry/exit buttons in a
        //      ``.diag-actions`` row + ``.diag-pre`` result block.
        // Reusing the OSC card visuals keeps the operator's mental
        // model consistent across the two diagnostics surfaces.
        html += '  <div class="row-tab-panel" id="row-tab-zone-diag" role="tabpanel">';
        html += '    <div class="diag-grid">';
        html += '      <div class="stat-panel diag-card">';
        html += '        <div class="stat-panel-head">';
        html += '          <h4 class="stat-panel-title">Live state</h4>';
        html += '        </div>';
        html += '        <div class="diag-body">';
        html += '          <dl class="diag-row"><dt>Occupants</dt><dd id="zone-diag-occupants" class="diag-empty">–</dd></dl>';
        html += '          <dl class="diag-row"><dt>Last event</dt><dd id="zone-diag-last-event" class="diag-empty">No events yet.</dd></dl>';
        html += '        </div>';
        html += '      </div>';
        html += '      <div class="stat-panel diag-card">';
        html += '        <div class="stat-panel-head">';
        html += '          <h4 class="stat-panel-title">Test send</h4>';
        html += '        </div>';
        html += '        <p class="stat-help">Force one packet on the chosen entry / exit address. Bypasses live-occupancy state so the address can be probed independently.</p>';
        html += '        <div class="diag-actions">';
        html += '          <button type="button" class="diag-action" data-zone-test="first">First Entry</button>';
        html += '          <button type="button" class="diag-action" data-zone-test="additional">Additional Entry</button>';
        html += '          <button type="button" class="diag-action" data-zone-test="partial">Partial Exit</button>';
        html += '          <button type="button" class="diag-action" data-zone-test="final">Final Exit</button>';
        html += '        </div>';
        html += '        <pre id="zone-diag-test-result" class="diag-pre" hidden></pre>';
        html += '      </div>';
        html += '    </div>';
        html += '  </div>';

        // ---- Action row at the bottom of all tabs ----
        html += '  <div class="actions">';
        html += '    <button type="button" class="save-btn" id="zone-save-btn">Save Zone</button>';
        html += '    <button type="button" class="secondary" id="zone-duplicate-btn">Duplicate</button>';
        html += '  </div>';
        html += '</div>';
        detailsEl.innerHTML = html;
        var saveBtn = document.getElementById('zone-save-btn');
        if (saveBtn) saveBtn.addEventListener('click', saveSelectedZone);
        var dupBtn = document.getElementById('zone-duplicate-btn');
        if (dupBtn) dupBtn.addEventListener('click', duplicateSelectedZone);
        // Wire colour swatch to hidden input for collectDetailFields.
        // attachColorPicker writes to trigger.dataset.value; mirror
        // that to hidden input on commit so Save picks up new colour.
        var colorTrigger = detailsEl.querySelector('button.color-swatch-trigger[data-color-picker]');
        if (colorTrigger) {
            var colorHidden = detailsEl.querySelector('input[type="hidden"][data-zone-field="color"]');
            window.OpenFollow.attachColorPicker(colorTrigger, {
                mode: colorTrigger.dataset.colorPicker || 'full',
                value: colorTrigger.dataset.value,
                onChange: function(hex) {
                    if (colorHidden) colorHidden.value = hex;
                },
            });
        }
        bindVertexInputs();
        bindOscFieldValidators();
        bindTriggeredByValidator();
        bindZoneTestSendButtons();
        renderZoneDiagnostics(z);
    }

    // triggered_by shares per-zone validation
    // endpoint that the OSC address fields use, but its parser is
    // ``_as_int_list`` (not ``_validate_osc_message``) so it gets a
    // separate handler that doesn't piggyback on
    // ``onOscFieldBlur``'s class detection.
    function bindTriggeredByValidator() {
        var inp = detailsEl.querySelector('[data-zone-validate-field="triggered_by"]');
        if (!inp) return;
        inp.addEventListener('blur', function() {
            var errSpan = detailsEl.querySelector('[data-zone-field-error="triggered_by"]');
            if (!errSpan) return;
            var url = '/api/validate/zone/triggered_by'
                + '?triggered_by=' + encodeURIComponent(inp.value);
            fetch(url, {cache: 'no-store'})
                .then(function(r) { return r.ok ? r.text() : ''; })
                .then(function(body) {
                    errSpan.innerHTML = body;
                    var hasError = body.indexOf('field-error-msg') !== -1;
                    inp.setAttribute('aria-invalid', hasError ? 'true' : 'false');
                })
                .catch(function() {
                    errSpan.innerHTML = '';
                    inp.setAttribute('aria-invalid', 'false');
                });
        });
    }

    function bindZoneTestSendButtons() {
        var btns = detailsEl.querySelectorAll('[data-zone-test]');
        btns.forEach(function(btn) {
            btn.addEventListener('click', onZoneTestSendClick);
        });
    }

    function onZoneTestSendClick(evt) {
        if (state.selectedIndex < 0) return;
        var which = evt.target.getAttribute('data-zone-test');
        if (!which) return;
        var idx = state.selectedIndex;
        var pre = document.getElementById('zone-diag-test-result');
        if (pre) {
            pre.textContent = 'Sending…';
            pre.removeAttribute('hidden');
        }
        // Use the same Save-then-Send pattern as OSC Bindings: persist
        // any pending edits first so the test fires the address the
        // operator actually sees in the field, not whatever was last
        // saved. saveSelectedZone() returns a Promise.
        saveSelectedZone().then(function() {
            var url = '/api/zones/' + idx + '/test_send'
                + '?which=' + encodeURIComponent(which);
            return fetch(url, {method: 'POST'});
        }).then(function(r) {
            return r.text().then(function(body) { return {ok: r.ok, body: body}; });
        }).then(function(res) {
            if (!pre) return;
            try {
                pre.textContent = JSON.stringify(JSON.parse(res.body), null, 2);
            } catch (_) {
                pre.textContent = res.body;
            }
        }).catch(function(err) {
            if (pre) pre.textContent = 'Error: ' + (err && err.message ? err.message : err);
        });
    }

    function renderZoneDiagnostics(z) {
        var occEl = document.getElementById('zone-diag-occupants');
        var lastEl = document.getElementById('zone-diag-last-event');
        if (!occEl || !lastEl) return;
        var diag = z.diagnostics || {};
        var occupants = diag.occupants || [];
        // Drop the ``diag-empty`` italic style as soon as we have real
        // values so the populated rows match the OSC Output Diagnostics
        // visual weight.
        if (occupants.length === 0) {
            occEl.textContent = 'none';
            occEl.classList.add('diag-empty');
        } else {
            var labels = occupants.map(function(o) { return o.kind + ' ' + o.id; });
            occEl.textContent = occupants.length + ' (' + labels.join(', ') + ')';
            occEl.classList.remove('diag-empty');
        }
        // Render timestamp alongside address so operator knows last fire.
        // Server ships relative last_event_age_seconds (time.monotonic
        // is process-relative); -1 means no event. Format ages >60s as
        // Mm Ss to avoid "473s ago".
        var addr = diag.last_event_address;
        var age = diag.last_event_age_seconds;
        if (addr && typeof age === 'number' && age >= 0) {
            lastEl.textContent = addr + ' (' + formatAge(age) + ' ago)';
            lastEl.classList.remove('diag-empty');
        } else if (addr) {
            // Address present but age missing – older API or pre-poll
            // refresh. Show the address alone rather than nothing.
            lastEl.textContent = addr;
            lastEl.classList.remove('diag-empty');
        } else {
            lastEl.textContent = 'none yet';
            lastEl.classList.add('diag-empty');
        }
    }

    function formatAge(seconds) {
        if (seconds < 1) return '<1s';
        if (seconds < 60) return Math.round(seconds) + 's';
        var m = Math.floor(seconds / 60);
        var s = Math.round(seconds - m * 60);
        return m + 'm ' + s + 's';
    }

    function duplicateSelectedZone() {
        if (state.selectedIndex < 0) return;
        var idx = state.selectedIndex;
        // Persist any pending edits before cloning so the copy matches
        // what the operator currently sees in the form.
        saveSelectedZone().then(function() {
            return fetch('/api/zones/' + idx + '/duplicate', {method: 'POST'});
        }).then(function(r) { return r.ok ? r.json() : null; })
          .then(function(data) {
              if (!data) return;
              if (typeof data.index === 'number') {
                  state.selectedIndex = data.index;
              }
              fetchZones();
          })
          .catch(function() {});
    }

    // Per-field blur validation for four zone OSC address fields.
    // JS-rendered editor (no HTMX), so use vanilla fetch against
    // /api/validate/zone/<field> and swap HTML into error containers.
    // Server-side FIELD_RULES["zone"] is authoritative.
    function oscField(label, name, value, placeholder) {
        var errId = 'zone-' + name.replace(/_/g, '-') + '-error';
        return '    <div class="field">'
            + '<label>' + label + '</label>'
            + '<input type="text" data-zone-field="' + name + '"'
            + ' data-zone-osc-field="' + name + '"'
            + ' value="' + escapeAttr(value) + '"'
            + ' placeholder="' + placeholder + '"'
            + ' aria-describedby="' + errId + '">'
            + '<span class="field-error" id="' + errId + '"'
            + ' data-zone-field-error="' + name + '" aria-live="polite"></span>'
            + '</div>';
    }

    function bindOscFieldValidators() {
        var inputs = detailsEl.querySelectorAll('[data-zone-osc-field]');
        inputs.forEach(function(inp) {
            inp.addEventListener('blur', onOscFieldBlur);
        });
    }

    function onOscFieldBlur(evt) {
        var inp = evt.target;
        var field = inp.getAttribute('data-zone-osc-field');
        if (!field) return;
        var errSpan = detailsEl.querySelector(
            '[data-zone-field-error="' + field + '"]'
        );
        if (!errSpan) return;
        var url = '/api/validate/zone/' + encodeURIComponent(field)
            + '?' + encodeURIComponent(field) + '=' + encodeURIComponent(inp.value);
        fetch(url, {cache: 'no-store'})
            .then(function(r) { return r.ok ? r.text() : ''; })
            .then(function(body) {
                // ``body`` is one of: empty (valid), a ``field-error-msg``
                // span (invalid – flips aria-invalid), or a
                // ``field-note-msg`` span (advisory – does not flip
                // aria-invalid). Mirror the section-partial behaviour
                // by checking the class name in the response.
                errSpan.innerHTML = body;
                var hasError = body.indexOf('field-error-msg') !== -1;
                inp.setAttribute('aria-invalid', hasError ? 'true' : 'false');
            })
            .catch(function() {
                // Network blip – clear error so stale message doesn't haunt
                // field. Save path's silent-preserve fallback catches malformed.
                errSpan.innerHTML = '';
                inp.setAttribute('aria-invalid', 'false');
            });
    }

    function bindVertexInputs() {
        var inputs = detailsEl.querySelectorAll('[data-vertex-field]');
        inputs.forEach(function(inp) {
            inp.addEventListener('input', onVertexInput);
            inp.addEventListener('change', onVertexCommit);
        });
        var dels = detailsEl.querySelectorAll('[data-vertex-delete]');
        dels.forEach(function(btn) {
            btn.addEventListener('click', onVertexDelete);
        });
    }

    function onVertexInput(evt) {
        if (state.selectedIndex < 0) return;
        var z = state.zones[state.selectedIndex];
        if (!z || !z.vertices) return;
        var idx = parseInt(evt.target.getAttribute('data-vertex-index'), 10);
        var axis = evt.target.getAttribute('data-vertex-field');
        if (!z.vertices[idx]) return;
        var num = IS_IMPERIAL ? parseLength(evt.target.value) : parseFloat(evt.target.value);
        if (!isFinite(num)) return;
        z.vertices[idx][axis === 'x' ? 0 : 1] = num;
        if (IS_IMPERIAL) updateVertexEcho(idx);
        render();
    }

    function updateVertexEcho(vertexIdx) {
        var z = state.zones[state.selectedIndex];
        if (!z || !z.vertices[vertexIdx]) return;
        var echo = detailsEl.querySelector('[data-vertex-echo="' + vertexIdx + '"]');
        if (echo) {
            echo.textContent = 'Stored: ' + metricEcho(z.vertices[vertexIdx][0])
                + ', ' + metricEcho(z.vertices[vertexIdx][1]);
        }
    }

    function onVertexCommit() {
        if (state.selectedIndex < 0) return;
        updateZoneVertices(state.selectedIndex);
    }

    function onVertexDelete(evt) {
        if (state.selectedIndex < 0) return;
        var z = state.zones[state.selectedIndex];
        if (!z || !z.vertices || z.vertices.length <= 3) return;
        var idx = parseInt(evt.target.getAttribute('data-vertex-delete'), 10);
        z.vertices.splice(idx, 1);
        renderDetails();
        render();
        updateZoneVertices(state.selectedIndex);
    }

    function escapeAttr(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
    }

    function collectDetailFields() {
        var out = {};
        var nodes = detailsEl.querySelectorAll('[data-zone-field]');
        nodes.forEach(function(n) {
            var name = n.getAttribute('data-zone-field');
            if (n.type === 'checkbox') out[name] = n.checked;
            else out[name] = n.value;
        });
        // triggered_by is comma-separated text input
        // in the form, but the JSON CRUD API expects ``list[int]``.
        // Parse here so the route layer's ``_parse_triggered_by`` sees
        // a list and not a raw string. Empty / whitespace-only field
        // becomes ``[]`` (= clear the filter).
        //
        // Parse strictly (regex check before parseInt so "1abc" doesn't
        // coerce to 1). Reject save if any token invalid (don't silently
        // drop: strict regex matches server-side _validate_int_list).
        if (typeof out.triggered_by === 'string') {
            var parsed = parseTriggeredBy(out.triggered_by);
            if (parsed === null) {
                // Sentinel: any invalid token. Caller (saveSelectedZone)
                // checks for this and surfaces the inline error instead
                // of submitting a partial filter.
                out.triggered_by_invalid = true;
                delete out.triggered_by;
            } else {
                out.triggered_by = parsed;
            }
        }
        return out;
    }

    var STRICT_INT_RE = /^[+-]?\d+$/;

    function parseTriggeredBy(raw) {
        var parts = raw.split(',');
        var parsed = [];
        for (var i = 0; i < parts.length; i++) {
            var t = parts[i].trim();
            if (!t) continue;
            if (!STRICT_INT_RE.test(t)) return null;
            parsed.push(parseInt(t, 10));
        }
        return parsed;
    }

    // Returns Promise once save round-trip lands (or rejects on error).
    // Chained from Duplicate and Test send so they reflect current
    // form state, not last-saved snapshot.
    function saveSelectedZone() {
        if (state.selectedIndex < 0) return Promise.resolve();
        var idx = state.selectedIndex;
        var z = state.zones[idx];
        if (!z) return Promise.resolve();
        var body = collectDetailFields();
        // Refuse to save when
        // ``triggered_by`` has any invalid tokens. Silently dropping
        // them (the previous behaviour) persisted a filter that didn't
        // match what the field still showed. Surface the same inline
        // error the blur validator emits so the operator notices the
        // problem at the moment they tried to save, then bail.
        if (body.triggered_by_invalid) {
            var inp = detailsEl.querySelector(
                '[data-zone-validate-field="triggered_by"]',
            );
            var errSpan = detailsEl.querySelector(
                '[data-zone-field-error="triggered_by"]',
            );
            if (inp && errSpan) {
                inp.setAttribute('aria-invalid', 'true');
                errSpan.innerHTML = '<span class="field-error-msg" '
                    + 'role="alert" aria-live="assertive">'
                    + 'Comma-separated marker IDs (e.g. 0, 1, 5).'
                    + '</span>';
            }
            return Promise.resolve();
        }
        body.vertices = z.vertices;
        return fetch('/api/zones/' + idx, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        }).then(function(r) {
            if (!r.ok) return r;
            // Persisted: clear the section's dirty flag so the
            // "Save as template" button re-enables. The per-vertex
            // input listener that fired during the edit set
            // ``data-dirty="1"``; we drop it explicitly here rather
            // than rely on a swap (zones don't HTMX-swap the section
            // on save – they just refetch into the same DOM).
            var section = document.getElementById('zone-editor-section');
            if (section && window.markFormClean) window.markFormClean(section);
            fetchZones();
            return r;
        });
    }

    function pickZoneColor() {
        // Delegate to shared palette helper. Marker catalog, Python
        // seeders, and zones walk same sequence; never collide.
        return window.OpenFollow.nextUnusedColor(
            state.zones.map(function(z) { return z.color; })
        );
    }

    function createZone(vertices) {
        var body = {
            name: 'Zone ' + (state.zones.length + 1),
            color: pickZoneColor(),
            trigger_source: 'markers',
            enabled: true,
            vertices: vertices,
        };
        fetch('/api/zones', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        }).then(function(r) { return r.ok ? r.json() : null; })
          .then(function(data) {
              if (data && typeof data.index === 'number') state.selectedIndex = data.index;
              fetchZones();
          });
    }

    function updateZoneVertices(idx) {
        var z = state.zones[idx];
        if (!z) return;
        fetch('/api/zones/' + idx, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({vertices: z.vertices}),
        });
    }

    function deleteSelected() {
        if (state.selectedIndex < 0) return;
        if (!confirm('Delete this zone?')) return;
        var idx = state.selectedIndex;
        fetch('/api/zones/' + idx, {method: 'DELETE'})
            .then(function(r) {
                if (r.ok) {
                    state.selectedIndex = -1;
                    fetchZones();
                }
            });
    }

    // Events
    addBtn.addEventListener('click', function() {
        state.draft = {vertices: []};
        state.selectedIndex = -1;
        render();
        renderDetails();
    });
    finishBtn.addEventListener('click', function() {
        if (state.draft && state.draft.vertices.length >= 3) {
            var verts = state.draft.vertices;
            state.draft = null;
            createZone(verts);
        }
    });
    cancelBtn.addEventListener('click', function() {
        state.draft = null;
        render();
    });
    deleteBtn.addEventListener('click', deleteSelected);

    // Save / Load template buttons. Modal helpers
    // live in ``base.tpl`` so the same UX is shared across every
    // section that gains template support. After a successful apply,
    // ``fetchZones()`` reloads the section so the editor reflects
    // the freshly-applied state without the operator refreshing.
    var templateSaveBtn = document.getElementById('zone-template-save-btn');
    if (templateSaveBtn) {
        templateSaveBtn.addEventListener('click', function() {
            window.saveCurrentSectionAsTemplate({
                type: 'zones',
                title: 'Save zones as template',
                placeholder: 'e.g. Studio A',
                onSaved: function() {},
            });
        });
    }
    var templateLoadBtn = document.getElementById('zone-template-load-btn');
    if (templateLoadBtn) {
        templateLoadBtn.addEventListener('click', function() {
            window.modalChooseTemplate({
                type: 'zones',
                title: 'Load zones template',
                applyConfirmMessage:
                    'Loading a zones template replaces the entire current'
                    + ' zones section. Continue?',
                onApplied: function() {
                    // Zones template replaces FULL trigger_zones section
                    // (including section-level defaults). fetchZones()
                    // only refreshes canvas, so defaults stay stale until
                    // manual refresh. Force full page reload to re-render
                    // all dependents against new on-disk state.
                    window.location.reload();
                },
            });
        });
    }

    canvas.addEventListener('mousedown', function(evt) {
        var m = mousePos(evt);
        var w = screenToWorld(m[0], m[1]);
        if (state.draft) return;
        // Try to drag a vertex of the selected zone
        var vi = findVertexAt(m[0], m[1]);
        if (vi !== null) {
            state.dragging = {zoneIndex: state.selectedIndex, vertexIndex: vi};
            // Freeze the viewport extent for the duration of the drag
            // so the canvas doesn't auto-zoom-out as the vertex moves
            // past the edge – that would yank the world coordinate
            // beneath the cursor mid-drag. ``endDrag`` clears this.
            state._frozenExtent = _computeViewExtentLive();
            return;
        }
        // Select a zone under cursor
        var zi = findZoneAt(w[0], w[1]);
        if (zi !== state.selectedIndex) {
            state.selectedIndex = zi;
            renderDetails();
        }
        render();
    });

    canvas.addEventListener('mousemove', function(evt) {
        if (!state.dragging) return;
        var m = mousePos(evt);
        var w = screenToWorld(m[0], m[1]);
        var z = state.zones[state.dragging.zoneIndex];
        if (z && z.vertices[state.dragging.vertexIndex]) {
            z.vertices[state.dragging.vertexIndex] = [w[0], w[1]];
            render();
            syncVertexInputs(state.dragging.zoneIndex, state.dragging.vertexIndex);
        }
    });

    function syncVertexInputs(zoneIdx, vertexIdx) {
        if (zoneIdx !== state.selectedIndex) return;
        var z = state.zones[zoneIdx];
        if (!z || !z.vertices[vertexIdx]) return;
        var xInp = detailsEl.querySelector('[data-vertex-field="x"][data-vertex-index="' + vertexIdx + '"]');
        var yInp = detailsEl.querySelector('[data-vertex-field="y"][data-vertex-index="' + vertexIdx + '"]');
        if (xInp) xInp.value = IS_IMPERIAL ? formatLength(z.vertices[vertexIdx][0]) : Number(z.vertices[vertexIdx][0]).toFixed(3);
        if (yInp) yInp.value = IS_IMPERIAL ? formatLength(z.vertices[vertexIdx][1]) : Number(z.vertices[vertexIdx][1]).toFixed(3);
        if (IS_IMPERIAL) updateVertexEcho(vertexIdx);
    }

    function endDrag() {
        if (!state.dragging) return;
        updateZoneVertices(state.dragging.zoneIndex);
        state.dragging = null;
        // Release the frozen viewport extent so the next render
        // re-fits the canvas to the new geometry – picks up the
        // vertex's final position if it landed beyond the previous
        // bounds.
        state._frozenExtent = null;
        render();
    }
    // Also listen on window so releasing the mouse outside the canvas still
    // clears the drag state; blur catches tab-away while holding the button.
    canvas.addEventListener('mouseup', endDrag);
    window.addEventListener('mouseup', endDrag);
    window.addEventListener('blur', endDrag);

    canvas.addEventListener('click', function(evt) {
        if (!state.draft) return;
        var m = mousePos(evt);
        var w = screenToWorld(m[0], m[1]);
        var verts = state.draft.vertices;
        // Close by clicking first vertex (within 10px)
        if (verts.length >= 3) {
            var p0 = worldToScreen(verts[0][0], verts[0][1]);
            var dx = p0[0] - m[0], dy = p0[1] - m[1];
            if (dx * dx + dy * dy <= 100) {
                var captured = verts.slice();
                state.draft = null;
                createZone(captured);
                return;
            }
        }
        verts.push([w[0], w[1]]);
        render();
    });

    canvas.addEventListener('dblclick', function() {
        if (state.draft && state.draft.vertices.length >= 3) {
            var verts = state.draft.vertices.slice();
            state.draft = null;
            createZone(verts);
        }
    });

    // Initial load + 1s polling. Tabs are hidden via CSS (not unmounted),
    // so we must additionally gate on visibility so a background tab doesn't
    // hammer /api/zones and re-parse the config every second.
    function isZoneEditorVisible() {
        if (document.hidden) return false;
        var section = document.getElementById('zone-editor-section');
        if (!section || !document.body.contains(section)) return false;
        return section.getClientRects().length > 0;
    }

    if (isZoneEditorVisible()) fetchZones();
    if (window.__zoneEditorTimer) clearInterval(window.__zoneEditorTimer);
    window.__zoneEditorTimer = setInterval(function() {
        if (!document.getElementById('zone-canvas')) {
            clearInterval(window.__zoneEditorTimer);
            window.__zoneEditorTimer = null;
            window.__zoneEditorInit = false;
            return;
        }
        if (isZoneEditorVisible()) fetchZones();
    }, 1000);
    // Refresh immediately when the tab becomes visible again so the user
    // doesn't have to wait up to 1s for the first paint after switching in.
    document.addEventListener('visibilitychange', function() {
        if (isZoneEditorVisible()) fetchZones();
    });
})();
</script>
