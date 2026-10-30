% from openfollow.web.routes import osc_destinations_script_json
% _osc_dests_json = osc_destinations_script_json(config)
<div id="zone-editor-section" class="section" data-feature="zones" data-fold-key="zone_editor" data-help="zone_editor" data-template-form="1">
    <div class="section-head">
        <h2>Zone Editor</h2>
    </div>

    <div class="group">
        <div class="seg-toggle plan-view-switch" id="zone-view-switch" role="radiogroup" aria-label="Zone editor view">
            <label class="seg-option">
                <input type="radio" name="zone_editor_view" value="plan" checked>
                <span><strong>Top-down</strong></span>
            </label>
            <label class="seg-option">
                <input type="radio" name="zone_editor_view" value="camera">
                <span><strong>Camera</strong></span>
            </label>
        </div>
        <div class="pv-stage" id="zone-picture" hidden>
            <img alt="Camera picture with the zones">
            <svg id="zone-picture-svg" viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"
                 tabindex="0" role="group" aria-label="Zone corners on the camera picture"></svg>
            <button type="button" class="pv-fs-btn" id="zone-fs-btn" aria-label="Full screen"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
        </div>
        <div class="pv-nofeed" id="zone-picture-nofeed" hidden>No video picture.</div>
        <div id="zone-canvas-wrap" style="position:relative;border:1px solid var(--border,#444);border-radius:6px;background:#111;overflow:hidden;width:100%;">
            <canvas id="zone-canvas" width="1280" height="720" style="display:block;width:100%;height:auto;background:#111;cursor:crosshair;"></canvas>
        </div>
        <div style="display:flex;gap:0.5rem;margin-top:0.5rem;flex-wrap:wrap;">
            <button type="button" id="zone-add-btn" class="save-btn">+ New Zone</button>
            <button type="button" id="zone-finish-btn" class="secondary" disabled>Finish Polygon</button>
            <button type="button" id="zone-cancel-btn" class="secondary" disabled>Cancel Drawing</button>
            <button type="button" id="zone-corner-delete-btn" class="secondary" hidden disabled>Delete corner</button>
            <button type="button" id="zone-delete-btn" class="danger" disabled>Delete zone</button>
            <button type="button" id="zone-refresh-btn" class="secondary" hidden>Refresh Image</button>
            <!-- Save / Load template buttons. Save captures entire
                 trigger_zones section (defaults + zones[]) as
                 .oftemplate under templates/user/. Load lists
                 all zones templates (system + user), confirms, then
                 replaces section (API requires ?confirm=1). The chooser
                 also exposes per-row Export + an Import action. -->
            <button type="button" id="zone-template-save-btn" class="secondary"
                    data-template-save
                    data-template-deps="#zone-editor-section, #trigger-zones-section">Save as template…</button>
            <button type="button" id="zone-template-load-btn" class="secondary">Load template…</button>
            <span id="zone-editor-status" class="section-note"></span>
        </div>
        <div id="zone-details" class="section-note" style="margin-top:1rem;">Select a zone to edit its OSC addresses and trigger source.</div>
    </div>
    <div class="pv-fs-shell" id="zone-fs-shell" hidden role="dialog" aria-modal="true" aria-label="Zone Editor, full screen">
        <div class="pv-fs-bar">
            <span class="pv-fs-title">Zone Editor</span>
            <span class="pv-fs-status" id="zone-fs-status"></span>
            <span class="pv-fs-actions">
                <button type="button" class="secondary small" id="zone-fs-refresh">Refresh Image</button>
                <button type="button" class="secondary small" id="zone-fs-corner-delete" disabled>Delete corner</button>
            </span>
            <button type="button" class="secondary small" id="zone-fs-exit">Exit full screen</button>
        </div>
        <div class="pv-fs-stage" id="zone-fs-stage"></div>
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
        selectedIndex: -1,
        detailsRenderedIndex: -2,  // sentinel: never rendered
    };

    var plan = new window.OpenFollow.PolygonPlan(canvas, {
        polygons: function() {
            return state.zones.map(function(z) { return (z && z.vertices) || []; });
        },
        editable: function() {
            var z = state.zones[state.selectedIndex];
            return (z && z.vertices) || null;
        },
        drawPolygons: renderZones,
        onPress: function(w) {
            var zi = findZoneAt(w[0], w[1]);
            if (zi !== state.selectedIndex) {
                state.selectedIndex = zi;
                renderDetails();
            }
        },
        onDragMove: function(vertexIdx) { syncVertexInputs(state.selectedIndex, vertexIdx); },
        onDragEnd: function() { updateZoneVertices(state.selectedIndex); },
        onDraftClose: createZone,
        onRender: function() { updateButtons(); updateCornerControls(); renderPicture(); },
    });

    // -- camera view: the zones as the saved camera sees them, projected in the browser ----
    var editorSection = document.getElementById('zone-editor-section');
    var pictureEl = document.getElementById('zone-picture');
    var canvasWrap = document.getElementById('zone-canvas-wrap');
    var refreshBtn = document.getElementById('zone-refresh-btn');
    var cornerDeleteBtn = document.getElementById('zone-corner-delete-btn');
    var fsShell = document.getElementById('zone-fs-shell');
    var pv = new window.OpenFollow.PictureView({
        stage: pictureEl, img: pictureEl.querySelector('img'), svg: document.getElementById('zone-picture-svg'),
        nofeed: document.getElementById('zone-picture-nofeed'),
        onSize: function() { pictureKey = ''; fsLayout(); render(); },
    });
    var mode = 'plan';
    // The saved camera from the zones poll, its model for the picture's size, and what the picture last drew.
    var camera = null, camModel = null, pictureKey = '';
    // Local corner edits and saves under way: a poll answer asked for before either is stale.
    var edits = 0, saving = 0;

    function floorZ() { return Number(plan.grid.z_offset) || 0; }
    function heightsOn() { return document.body.classList.contains('feature-zone_heights'); }
    // The plane a zone is drawn and picked on: the stage plane, raised by the zone's Z offset with zone heights on.
    function zonePlane(zone) { return floorZ() + (heightsOn() ? Number(zone.z_offset) || 0 : 0); }

    function cameraModel() {
        if (!camera) return null;
        if (!camModel || camModel.of !== camera || camModel.w !== pv.w || camModel.h !== pv.h) {
            camModel = new window.OpenFollow.CameraModel(camera, pv.w, pv.h);
            camModel.of = camera;
        }
        return camModel;
    }
    // Chords per straight stage edge when the lens bows it, as the Operator Screen draws it.
    function chords(cam) { return cam.lens ? 24 : 1; }
    function edgePoints(cam, zone, i) {
        var v = zone.vertices, a = v[i], b = v[(i + 1) % v.length], z = zonePlane(zone);
        return cam.segment([a[0], a[1], z], [b[0], b[1], z], chords(cam));
    }
    function outline(cam, zone) {
        var out = [];
        zone.vertices.forEach(function(_, i) { out = out.concat(edgePoints(cam, zone, i).slice(i ? 1 : 0)); });
        return out;
    }
    function chosenZone() {
        var zone = state.zones[state.selectedIndex];
        return zone && zone.vertices ? zone : null;
    }

    var corners = new window.OpenFollow.PicturePolygon(pv, {
        min: 3,
        corners: function() {
            var zone = chosenZone(), cam = cameraModel();
            if (!zone || !cam || mode !== 'camera') return null;
            var z = zonePlane(zone);
            return zone.vertices.map(function(p) { return cam.project([p[0], p[1], z]); });
        },
        edge: function(i) { return edgePoints(cameraModel(), chosenZone(), i); },
        place: function(i, at) {
            var zone = chosenZone(), w = cameraModel().unproject(at, zonePlane(zone));
            if (!w) return false;
            zone.vertices[i] = w;
            edits++;
            syncVertexInputs(state.selectedIndex, i);
            return true;
        },
        insert: function(i, at) {
            var zone = chosenZone(), w = cameraModel().unproject(at, zonePlane(zone));
            if (!w) return false;
            zone.vertices.splice(i + 1, 0, w);
            edits++;
            renderDetails();
            return true;
        },
        remove: function(i) {
            chosenZone().vertices.splice(i, 1);
            edits++;
            renderDetails();
            return true;
        },
        snapshot: function() { return JSON.stringify(chosenZone().vertices); },
        restore: function(snap) {
            chosenZone().vertices = JSON.parse(snap);
            edits++;
            renderDetails();
        },
        readout: function(i) {
            var zone = chosenZone(), v = zone.vertices[i];
            return 'V' + (i + 1) + ' · X ' + formatLength(v[0]) + ' · Y ' + formatLength(v[1])
                + (heightsOn() ? ' · Z ' + formatLength(zonePlane(zone)) : '');
        },
        changed: render,
        save: function() { updateZoneVertices(state.selectedIndex); },
        press: function(ev) {
            var line = ev.target && ev.target.closest && ev.target.closest('[data-zone]');
            if (!line || +line.dataset.zone === state.selectedIndex) return;
            state.selectedIndex = +line.dataset.zone;
            renderDetails();
        },
    });

    function renderPicture() {
        if (mode !== 'camera') return;
        var cam = cameraModel(), k = pv.cssScale();
        var shapes = state.zones.map(function(z) { return [z.vertices, z.color, z.z_offset]; });
        var key = JSON.stringify([shapes, state.selectedIndex, corners.chosen, corners.keyboard, camera, floorZ(), heightsOn(),
            pv.w, pv.h, k]);
        if (key === pictureKey) return;
        pictureKey = key;
        pv.clear();
        if (!cam) { pv.hideLoupe(); return; }
        state.zones.forEach(function(zone, i) {
            if (!zone || !zone.vertices || zone.vertices.length < 3) return;
            var col = zone.color || '#ff8000';
            pv.polylines(outline(cam, zone)).forEach(function(run) {
                var line = pv.el('polyline', {
                    points: run, stroke: col, fill: col, 'vector-effect': 'non-scaling-stroke',
                    'class': 'pv-zone' + (i === state.selectedIndex ? ' pv-zone-selected' : ''),
                });
                line.dataset.zone = i;
                pv.svg.appendChild(line);
            });
        });
        corners.draw();
    }

    function updateCornerControls() {
        var zone = chosenZone(), can = corners.canDelete();
        cornerDeleteBtn.disabled = !can;
        document.getElementById('zone-fs-corner-delete').disabled = !can;
        var name = zone && (zone.name || 'Zone ' + (state.selectedIndex + 1));
        document.getElementById('zone-fs-status').textContent = zone ? name + ' · ' + zone.vertices.length + ' corners' : 'No zone chosen';
    }

    // Full screen: the picture moves into a shell over the page, which sits in <body> while open because a
    // section's backdrop filter would hold a fixed child inside it.
    var fsHolder = null;
    function fsLayout() {
        if (fsShell.hidden) return;
        var box = document.getElementById('zone-fs-stage').getBoundingClientRect();
        if (box.width > 0 && box.height > 0) pictureEl.style.width = Math.min(box.width, box.height * pv.w / pv.h) + 'px';
        pictureKey = '';
        render();
    }
    function openFull() {
        if (!fsShell.hidden) return;
        fsHolder = document.createElement('div');
        fsHolder.style.height = pictureEl.getBoundingClientRect().height + 'px';
        pictureEl.parentNode.insertBefore(fsHolder, pictureEl);
        document.body.appendChild(fsShell);
        document.getElementById('zone-fs-stage').appendChild(pictureEl);
        fsShell.hidden = false;
        document.getElementById('zone-fs-btn').hidden = true;
        document.body.style.overflow = 'hidden';
        fsLayout();
        pv.svg.focus({ preventScroll: true });
    }
    function closeFull() {
        if (fsShell.hidden) return;
        corners.cancelDrag();
        corners.choose(null);
        fsHolder.parentNode.insertBefore(pictureEl, fsHolder);
        fsHolder.remove();
        fsHolder = null;
        pictureEl.style.width = '';
        fsShell.hidden = true;
        document.getElementById('zone-fs-btn').hidden = false;
        editorSection.appendChild(fsShell);
        document.body.style.overflow = '';
        pictureKey = '';
        render();
        document.getElementById('zone-fs-btn').focus({ preventScroll: true });
    }
    document.getElementById('zone-fs-btn').addEventListener('click', openFull);
    document.getElementById('zone-fs-exit').addEventListener('click', closeFull);
    document.getElementById('zone-fs-refresh').addEventListener('click', function() { pv.load(); });
    cornerDeleteBtn.addEventListener('click', function() { corners.deleteChosen(); });
    document.getElementById('zone-fs-corner-delete').addEventListener('click', function() { corners.deleteChosen(); });
    // Esc from a bar button leaves; on the picture it lets go of the corner first.
    fsShell.addEventListener('keydown', function(ev) {
        if (ev.defaultPrevented) return;
        if (ev.key === 'Escape') { closeFull(); ev.preventDefault(); return; }
        if (ev.key !== 'Tab') return;
        // Tab stays inside the full-screen view.
        var all = Array.prototype.filter.call(fsShell.querySelectorAll('button, [tabindex="0"]'), function(el) {
            return !el.disabled && el.offsetParent !== null;
        });
        var first = all[0], last = all[all.length - 1];
        if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
        else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    });
    window.addEventListener('resize', function() {
        if (!document.body.contains(pictureEl)) return;
        fsLayout();
        pictureKey = '';
        render();
    });

    function applyMode(next) {
        mode = next;
        var onCamera = mode === 'camera';
        canvasWrap.hidden = onCamera;
        refreshBtn.hidden = !onCamera;
        cornerDeleteBtn.hidden = !onCamera;
        if (onCamera) {
            if (plan.draft) plan.cancelDraft();
            pictureKey = '';
            pv.load();
        } else {
            corners.cancelDrag();
            corners.choose(null);
            pictureEl.hidden = true;
            document.getElementById('zone-picture-nofeed').hidden = true;
        }
        render();
    }
    refreshBtn.addEventListener('click', function() { pv.load(); });

    function fetchZones() {
        var asked = edits;
        fetch('/api/zones', {cache: 'no-store'})
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                // A corner in hand, or edited or saved since this was asked, is newer than the answer.
                if (!data || asked !== edits || saving || corners.busy()) return;
                // Don't replace the selected zone's data while the user is
                // typing into its detail inputs (would clobber in-progress
                // edits – in particular per-vertex coordinate fields).
                var activeEl = document.activeElement;
                var editingSelected = activeEl && detailsEl.contains(activeEl) && state.selectedIndex >= 0;
                var incomingZones = data.zones || [];
                // The station's copy of the selected zone, before an edit in progress is put back over it.
                var stored = incomingZones[state.selectedIndex];
                if (editingSelected && state.zones[state.selectedIndex]) {
                    var preserved = state.zones[state.selectedIndex];
                    // Keep the page's object (the write queue finds zones by identity) and its corners, but take
                    // the station's other fields, which the picture reads; the inputs being typed in are untouched.
                    if (stored) {
                        Object.keys(stored).forEach(function(k) {
                            if (k !== 'vertices') preserved[k] = stored[k];
                        });
                    }
                    state.zones = incomingZones;
                    if (state.selectedIndex < state.zones.length) {
                        state.zones[state.selectedIndex] = preserved;
                    }
                } else {
                    state.zones = incomingZones;
                }
                plan.markers = data.markers || [];
                if (data.grid) plan.grid = data.grid;
                camera = data.camera || null;
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
                    if (stored) {
                        syncHeightsShown(stored);
                        renderZoneDiagnostics(stored);
                    }
                }
            })
            .catch(function() {});
    }

    function renderZones() {
        for (var i = 0; i < state.zones.length; i++) {
            var z = state.zones[i];
            if (!z.vertices || z.vertices.length < 2) continue;
            var isSel = (i === state.selectedIndex);
            var isOcc = !!z.is_occupied;
            plan.tracePolygon(z.vertices);

            var col = z.color || '#ff8000';
            ctx.fillStyle = hexToRgba(col, isOcc ? 0.38 : 0.18);
            if (z.vertices.length >= 3) ctx.fill();
            ctx.strokeStyle = hexToRgba(col, isSel ? 1.0 : 0.8);
            ctx.lineWidth = isSel ? 2.5 : (isOcc ? 2.0 : 1.25);
            ctx.stroke();

            if (isSel) plan.drawHandles(z.vertices, col);

            // Label
            if (z.name) {
                var cx = 0, cy = 0;
                for (var m = 0; m < z.vertices.length; m++) {
                    var pv = plan.toScreen(z.vertices[m][0], z.vertices[m][1]);
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

    function render() {
        plan.render();
    }

    function updateButtons() {
        var drawing = !!plan.draft;
        finishBtn.disabled = !(drawing && plan.draft.vertices.length >= 3);
        cancelBtn.disabled = !drawing;
        deleteBtn.disabled = (state.selectedIndex < 0);
        addBtn.disabled = drawing || mode === 'camera';
        // The chosen zone is named by its details heading, so the line only speaks while drawing.
        statusEl.textContent = drawing
            ? 'Drawing: ' + plan.draft.vertices.length + ' point(s). Click first vertex or double-click to close.'
            : '';
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

    function findZoneAt(wx, wy) {
        for (var i = state.zones.length - 1; i >= 0; i--) {
            var z = state.zones[i];
            if (z.vertices && z.vertices.length >= 3 && window.OpenFollow.pointInPolygon(wx, wy, z.vertices)) {
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
        html += '      <div class="field checkbox-field"><label for="zone-detail-enabled">Zone</label>' +
            '<div class="checkbox-wrap checkbox-field inline">' +
            '<input type="checkbox" id="zone-detail-enabled" data-zone-field="enabled" ' + (z.enabled ? 'checked' : '') + '>' +
            '<label for="zone-detail-enabled">Use this zone</label></div></div>';
        html += '    </div>';
        html += '    <div class="row">';
        html += '      <div class="field"><label>Trigger Source</label><select data-zone-field="trigger_source">';
        html += '        <option value="markers" ' + (z.trigger_source === 'markers' ? 'selected' : '') + '>Markers (PSN)</option>';
        html += '        <option value="detection" ' + (z.trigger_source === 'detection' ? 'selected' : '') + '>Detection (AI)</option>';
        html += '        <option value="both" ' + (z.trigger_source === 'both' ? 'selected' : '') + '>Both</option>';
        html += '      </select></div>';
        html += '      <div class="field" style="flex:2 1 0;">';
        html += '        <label>Triggered By</label>';
        html += '        <input type="text" data-zone-field="triggered_by"'
              + ' data-zone-validate-field="triggered_by"'
              + ' value="' + escapeAttr((z.triggered_by || []).join(', ')) + '"'
              + ' placeholder="e.g. 0, 1, 5"'
              + ' aria-describedby="zone-triggered-by-error">';
        html += '        <span class="field-error" id="zone-triggered-by-error" data-zone-field-error="triggered_by" aria-live="polite"></span>';
        html += '      </div>';
        html += '    </div>';
        // Zone heights: shown only while that feature is on.
        html += '    <div class="row" data-feature="zone_heights">';
        html += blurField('Z offset (' + vertexUnitLabel() + ')', 'z_offset',
            heightShown('z_offset', z), IS_IMPERIAL ? 'text' : 'number', '0.01');
        html += '      <div class="field checkbox-field"><label for="zone-detail-raise-markers">Marker height</label>' +
            '<div class="checkbox-wrap checkbox-field inline">' +
            '<input type="checkbox" id="zone-detail-raise-markers" data-zone-field="raise_markers" ' + (z.raise_markers ? 'checked' : '') + '>' +
            '<label for="zone-detail-raise-markers">Add the Z offset inside this zone</label></div></div>';
        html += blurField('Height transition time (s)', 'height_transition_s',
            heightShown('height_transition_s', z), 'number', '0.1');
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
            html += '          <button type="button" class="danger" data-vertex-delete="' + vi + '" title="Remove vertex" ' + canDel + '>×</button>';
            html += '        </div>';
            if (IS_IMPERIAL) {
                html += '          <small class="metric-echo" data-vertex-echo="' + vi + '">Stored: ' + metricEcho(vx) + ', ' + metricEcho(vy) + '</small>';
                html += '        </div>';
            }
        }
        html += '      </div>';
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
        var z = state.zones[state.selectedIndex];
        var pre = document.getElementById('zone-diag-test-result');
        var near = detailsEl.querySelector('.actions'), saveError = window.OpenFollow.saveError;
        // Under the zone's actions, or under the buttons once its details are gone.
        var notSent = function(info) {
            if (pre) pre.setAttribute('hidden', '');
            saveError.show(editorSection, info, 'Not sent.', near && near.isConnected ? near : deleteBtn.parentNode);
        };
        if (pre) {
            pre.textContent = 'Sending…';
            pre.removeAttribute('hidden');
        }
        // Use the same Save-then-Send pattern as OSC Bindings: persist
        // any pending edits first so the test fires the address the
        // operator actually sees in the field, not whatever was last
        // saved. saveSelectedZone() returns a Promise.
        saveSelectedZone().then(function(saved) {
            if (!saved) return null;
            return zoneJob(z, function(at) {
                var url = '/api/zones/' + at + '/test_send' + '?which=' + encodeURIComponent(which);
                return timedFetch(url, {method: 'POST'}).then(function(r) {
                    return r.text().then(function(body) { return {ok: r.ok, body: body}; });
                });
            });
        }).then(function(res) {
            if (!res && state.zones.indexOf(z) < 0) {
                notSent({error: 'The zone was deleted before the test was sent.', action: '', href: ''});
                return;
            }
            if (!pre) return;
            if (!res) {
                // Not sent: the line under the actions says why the save failed.
                pre.setAttribute('hidden', '');
                return;
            }
            try {
                pre.textContent = JSON.stringify(JSON.parse(res.body), null, 2);
            } catch (_) {
                pre.textContent = res.body;
            }
        }).catch(function() { notSent(saveError.UNREACHABLE_ACTION); });
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
        var z = state.zones[state.selectedIndex], near = detailsEl.querySelector('.actions');
        // Persist any pending edits before cloning so the copy matches
        // what the operator currently sees in the form.
        saveSelectedZone().then(function(saved) {
            if (!saved) return null;
            return zoneJob(z, function(at) { return timedWrite('/api/zones/' + at + '/duplicate', {method: 'POST'}, near); });
        }).then(function(r) { return r ? r.json() : null; })
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

    // A zone height field's text for the value the station holds.
    function heightShown(name, z) {
        if (name !== 'z_offset') return String(z[name]);
        var metres = Number(z.z_offset) || 0;
        return IS_IMPERIAL ? formatLength(metres) : metres.toFixed(3);
    }

    // What the station holds now: an untouched field is left out of a save, so it must not compare
    // against a value an earlier save already replaced.
    function syncHeightsShown(z) {
        detailsEl.querySelectorAll('[data-zone-blur-field]').forEach(function(inp) {
            inp.setAttribute('data-shown', heightShown(inp.getAttribute('data-zone-blur-field'), z));
        });
    }

    // A number field validated on blur like the OSC addresses; ``data-shown``
    // keeps what it showed, so an untouched one is not saved back rounded.
    function blurField(label, name, value, type, step) {
        var id = 'zone-detail-' + name.replace(/_/g, '-');
        return '      <div class="field">'
            + '<label for="' + id + '">' + label + '</label>'
            + '<input type="' + type + '"' + (type === 'number' ? ' step="' + step + '"' : '')
            + ' id="' + id + '" data-zone-field="' + name + '"'
            + ' data-zone-blur-field="' + name + '"'
            + ' value="' + escapeAttr(value) + '" data-shown="' + escapeAttr(value) + '"'
            + ' aria-describedby="' + id + '-error">'
            + '<span class="field-error" id="' + id + '-error"'
            + ' data-zone-field-error="' + name + '" aria-live="polite"></span>'
            + '</div>';
    }

    function bindOscFieldValidators() {
        var inputs = detailsEl.querySelectorAll('[data-zone-osc-field], [data-zone-blur-field]');
        inputs.forEach(function(inp) {
            inp.addEventListener('blur', onOscFieldBlur);
        });
    }

    function onOscFieldBlur(evt) {
        var inp = evt.target;
        var field = inp.getAttribute('data-zone-osc-field') || inp.getAttribute('data-zone-blur-field');
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
            else if (n.value !== n.getAttribute('data-shown')) out[name] = n.value;
        });
        // Metres on the wire; a length the parser refuses keeps the saved one.
        if (IS_IMPERIAL && typeof out.z_offset === 'string') {
            var zMetres = parseLength(out.z_offset);
            if (isFinite(zMetres)) out.z_offset = zMetres;
        }
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

    // Resolves to the response when the write landed; otherwise shows why on
    // the editor (under ``near``, default its last actions row) and resolves
    // to null.
    function zoneWrite(request, near) {
        var saveError = window.OpenFollow.saveError;
        var editor = document.getElementById('zone-editor-section');
        return request.then(async function(r) {
            if (r.ok) {
                saveError.clear(editor);
                return r;
            }
            saveError.show(editor, await saveError.fromResponse(r), 'Not saved.', near);
            return null;
        }, function() {
            saveError.show(editor, saveError.UNREACHABLE, 'Not saved.', near);
            return null;
        });
    }

    // Resolves the save's Response, or null when nothing was saved.
    // Duplicate and Test send chain on it and stop on null, so they act
    // on the form the operator sees, never an older saved state.
    function saveSelectedZone() {
        if (state.selectedIndex < 0) return Promise.resolve(null);
        var z = state.zones[state.selectedIndex];
        if (!z) return Promise.resolve(null);
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
            return Promise.resolve(null);
        }
        var near = detailsEl.querySelector('.actions');
        return zoneJob(z, function(at) {
            body.vertices = z.vertices;
            return timedWrite('/api/zones/' + at, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            }, near);
        }).then(function(r) {
            if (!r) return r;
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
        zoneWrite(fetch('/api/zones', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        })).then(function(r) { return r ? r.json() : null; })
          .then(function(data) {
              if (data && typeof data.index === 'number') state.selectedIndex = data.index;
              fetchZones();
          });
    }

    // Corner saves and zone deletes go out one at a time, in the order asked, each finding its zone's index as
    // it is sent: an older save cannot land last, and none lands on the zone that moved into a deleted one's
    // place. A save asked while its zone's is still queued is that one, sent with the corners as they are then.
    // A write with no answer is given up, so the poll does not wait forever.
    var WRITE_TIMEOUT_MS = 10000;
    var writes = Promise.resolve(), queuedSaves = [];
    // Resolves (or rejects) with what ``job`` gave once the queue has counted it done.
    function enqueue(job) {
        saving++;
        var mine = writes.then(job);
        var after = mine.then(function() {}, function() {}).then(function() { saving--; edits++; });
        writes = after;
        return mine.then(function(r) { return after.then(function() { return r; }); },
            function(err) { return after.then(function() { throw err; }); });
    }
    // The job for the zone object ``zone``, sent to its index at that moment; null when it is gone by then.
    function zoneJob(zone, send) {
        return enqueue(function() {
            var at = state.zones.indexOf(zone);
            return at < 0 ? null : send(at);
        });
    }
    function timedFetch(url, opts) {
        var ctl = new AbortController();
        setTimeout(function() { ctl.abort(); }, WRITE_TIMEOUT_MS);
        opts.signal = ctl.signal;
        return fetch(url, opts);
    }
    function timedWrite(url, opts, near) { return zoneWrite(timedFetch(url, opts), near); }
    function updateZoneVertices(idx) {
        var zone = state.zones[idx];
        if (!zone || queuedSaves.indexOf(zone) >= 0) return;
        queuedSaves.push(zone);
        enqueue(function() {
            queuedSaves.splice(queuedSaves.indexOf(zone), 1);
            var at = state.zones.indexOf(zone);
            if (at < 0) return null;
            return timedWrite('/api/zones/' + at, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({vertices: zone.vertices}),
            });
        });
    }

    // Behind every corner save asked before it; the zone leaves the page's list before any later save is sent.
    async function deleteSelected() {
        if (state.selectedIndex < 0) return;
        corners.cancelDrag();
        corners.flush();
        var idx = state.selectedIndex;
        var ok = await modalConfirm({
            title: 'Delete zone?',
            message: 'Delete this zone?',
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!ok || state.selectedIndex !== idx) return;
        zoneJob(state.zones[idx], function(at) {
            // Said under the buttons: the zone's details may be gone by then.
            return timedWrite('/api/zones/' + at, {method: 'DELETE'}, deleteBtn.parentNode).then(function(r) {
                if (!r) return;
                state.zones.splice(at, 1);
                if (state.selectedIndex === at) state.selectedIndex = -1;
                else if (state.selectedIndex > at) state.selectedIndex--;
                // Another zone's details being typed into are kept, as the poll keeps them.
                var a = document.activeElement, title = detailsEl.querySelector('.group-title');
                if (state.selectedIndex >= 0 && a && detailsEl.contains(a)) {
                    state.detailsRenderedIndex = state.selectedIndex;
                    if (title) title.textContent = 'Zone ' + (state.selectedIndex + 1) + ' Details';
                } else renderDetails();
                render();
            });
        }).then(fetchZones);
    }

    // Events
    addBtn.addEventListener('click', function() {
        state.selectedIndex = -1;
        plan.startDraft();
        renderDetails();
    });
    finishBtn.addEventListener('click', function() { plan.finishDraft(); });
    cancelBtn.addEventListener('click', function() { plan.cancelDraft(); });
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

    // Initial load + 1s polling while the editor is in view (poll-gate.js).
    function isZoneEditorVisible() {
        var section = document.getElementById('zone-editor-section');
        return !!section && document.body.contains(section) && pollShown(section);
    }

    window.OpenFollow.viewSwitch(document.getElementById('zone-view-switch'), 'zones', applyMode);
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
    // Catch up at once when the editor comes into view; a replaced editor lets go.
    function onShown() {
        if (!document.body.contains(canvas)) document.body.removeEventListener('poll-shown', onShown);
        else if (isZoneEditorVisible()) fetchZones();
    }
    document.body.addEventListener('poll-shown', onShown);
})();
</script>
