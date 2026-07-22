<div id="detection-mask-editor" class="section" data-fold-key="detection_masks" data-help="detection_mask_editor">
    <div class="section-head">
        <h2>{{_('Detection Masks')}} <span class="badge-experimental">{{_('Experimental')}}</span></h2>
        <span class="section-note">{{_('Limit detection to regions you draw on the camera image.')}}</span>
    </div>

    <div class="dme-master">
        <label class="dme-switch">
            <input type="checkbox" data-dme="enabled">
            <span>{{_('Apply masks')}}</span>
        </label>
        <span class="dme-master-note" data-dme="enabledNote" aria-live="polite"></span>
    </div>

    <div class="dme-toolbar">
        <button type="button" class="dme-btn" data-dme="new">{{_('+ New Mask')}}</button>
        <button type="button" class="dme-btn dme-hidden" data-dme="finish">{{_('Finish Polygon')}}</button>
        <button type="button" class="dme-btn dme-hidden" data-dme="cancel">{{_('Cancel')}}</button>
        <button type="button" class="dme-btn" data-dme="delete" disabled>{{_('Delete Selected')}}</button>
        <button type="button" class="dme-btn" data-dme="refresh">{{_('Refresh Image')}}</button>
    </div>

    <div class="dme-stage" data-dme="stage">
        <img id="dme-img" alt="{{_('Camera snapshot')}}">
        <svg id="dme-svg" viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"></svg>
    </div>
    <div class="dme-no-feed" data-dme="nofeed" hidden>{{_('No video feed available. Configure a video source, then press')}} <strong>{{_('Refresh Image')}}</strong>.</div>

    <div class="dme-hint" data-dme="hint" aria-live="polite"></div>
    <ul class="dme-list" data-dme="list"></ul>
</div>

<style>
    /* When collapsed, hide every child but the header. The per-element rules
       below are ID-scoped and out-specify the base .section collapse rule, so
       restate it here at matching specificity. */
    #detection-mask-editor.is-collapsed > :not(.section-head) { display: none; }
    #detection-mask-editor .dme-master { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 12px; }
    #detection-mask-editor .dme-switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.9rem; }
    #detection-mask-editor .dme-switch input { width: 16px; height: 16px; cursor: pointer; }
    #detection-mask-editor .dme-master-note { font-size: 0.8rem; color: rgba(255,255,255,0.6); }
    #detection-mask-editor .dme-toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
    #detection-mask-editor .dme-btn {
        padding: 6px 12px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.18);
        background: rgba(255,255,255,0.06); color: inherit; font-size: 0.85rem; cursor: pointer;
    }
    #detection-mask-editor .dme-btn:hover { background: rgba(255,255,255,0.12); }
    #detection-mask-editor .dme-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    #detection-mask-editor .dme-stage {
        position: relative; width: 100%; max-width: 960px; line-height: 0;
        border-radius: 8px; overflow: hidden; background: #000;
    }
    #detection-mask-editor .dme-hidden { display: none; }
    #detection-mask-editor #dme-img { display: block; width: 100%; height: auto; }
    #detection-mask-editor #dme-svg {
        position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: crosshair;
    }
    #detection-mask-editor .dme-no-feed {
        padding: 24px 12px; border: 1px dashed rgba(255,255,255,0.22); border-radius: 8px;
        text-align: center; color: rgba(255,255,255,0.7); font-size: 0.85rem; background: rgba(255,255,255,0.03);
    }
    #detection-mask-editor .dme-hint { min-height: 1.1em; margin: 8px 0; font-size: 0.8rem; color: rgba(180,210,255,0.85); }
    #detection-mask-editor .dme-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
    #detection-mask-editor .dme-row {
        display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-radius: 6px;
        border: 1px solid rgba(255,255,255,0.12); background: rgba(255,255,255,0.04);
    }
    #detection-mask-editor .dme-row.dme-selected { border-color: rgba(120,200,120,0.7); background: rgba(120,200,120,0.10); }
    #detection-mask-editor .dme-row input[type="text"] {
        flex: 1 1 auto; min-width: 0; background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.14);
        border-radius: 4px; color: inherit; padding: 4px 6px; font-size: 0.85rem;
    }
    #detection-mask-editor .dme-row .dme-count { font-size: 0.75rem; color: rgba(255,255,255,0.5); white-space: nowrap; }
    #detection-mask-editor .dme-row button {
        background: none; border: none; color: rgba(255,160,160,0.9); cursor: pointer; font-size: 0.85rem; padding: 2px 6px;
    }
</style>

<script>
(function() {
    var root = document.getElementById('detection-mask-editor');
    if (!root) return;

    var svg = root.querySelector('#dme-svg');
    var img = root.querySelector('#dme-img');
    var stage = root.querySelector('[data-dme="stage"]');
    var noFeed = root.querySelector('[data-dme="nofeed"]');
    var hintEl = root.querySelector('[data-dme="hint"]');
    var listEl = root.querySelector('[data-dme="list"]');
    var btnNew = root.querySelector('[data-dme="new"]');
    var btnFinish = root.querySelector('[data-dme="finish"]');
    var btnCancel = root.querySelector('[data-dme="cancel"]');
    var btnDelete = root.querySelector('[data-dme="delete"]');
    var btnRefresh = root.querySelector('[data-dme="refresh"]');
    var chkEnabled = root.querySelector('[data-dme="enabled"]');
    var enabledNote = root.querySelector('[data-dme="enabledNote"]');

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var masks = [];      // [{name, vertices:[[nx,ny],...], enabled}]
    var selected = -1;   // selected mask index, -1 = none
    var draft = null;    // [[nx,ny],...] while drawing, else null
    var imgW = 1280, imgH = 720;
    var snapshotUrl = null;
    var drag = null;     // {mask, vertex} during a vertex drag
    var CLOSE_PX = 12;   // screen-px proximity to first vertex that closes a polygon
    var DEDUP_PX = 6;    // ignore a click this close to the previous point (de-bounces double-click)

    // -- coordinate helpers (viewBox is 1:1 with the snapshot pixels) --
    function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
    function clientToNorm(evt) {
        var ctm = svg.getScreenCTM();
        if (!ctm) return null;
        var pt = svg.createSVGPoint();
        pt.x = evt.clientX; pt.y = evt.clientY;
        var p = pt.matrixTransform(ctm.inverse());
        return [clamp01(p.x / imgW), clamp01(p.y / imgH)];
    }
    function normToScreen(normPt) {
        var ctm = svg.getScreenCTM();
        if (!ctm) return null;
        var pt = svg.createSVGPoint();
        pt.x = normPt[0] * imgW; pt.y = normPt[1] * imgH;
        return pt.matrixTransform(ctm);
    }
    function screenDist(normPt, evt) {
        var s = normToScreen(normPt);
        if (!s) return Infinity;
        var dx = s.x - evt.clientX, dy = s.y - evt.clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }

    // -- rendering --
    function el(name, attrs) {
        var n = document.createElementNS(SVG_NS, name);
        for (var k in attrs) { n.setAttribute(k, attrs[k]); }
        return n;
    }
    function pointsAttr(verts) {
        return verts.map(function(v) { return (v[0] * imgW) + ',' + (v[1] * imgH); }).join(' ');
    }
    function render() {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        masks.forEach(function(m, i) {
            if (m.vertices.length < 2) return;
            var isSel = (i === selected);
            var color = m.enabled ? '#5ad17a' : '#9aa0a6';
            var poly = el('polygon', {
                points: pointsAttr(m.vertices),
                fill: m.enabled ? 'rgba(90,209,122,0.18)' : 'rgba(154,160,166,0.10)',
                stroke: color,
                'stroke-width': isSel ? 3 : 2,
                'stroke-dasharray': m.enabled ? '' : '8 6',
                'vector-effect': 'non-scaling-stroke'
            });
            if (!draft) {
                poly.style.cursor = 'pointer';
                poly.addEventListener('click', function(ev) { ev.stopPropagation(); select(i); });
            }
            svg.appendChild(poly);
            if (isSel && !draft) {
                m.vertices.forEach(function(v, vi) {
                    var h = el('circle', {
                        cx: v[0] * imgW, cy: v[1] * imgH, r: 7,
                        fill: '#fff', stroke: color, 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke'
                    });
                    h.style.cursor = 'grab';
                    h.addEventListener('pointerdown', function(ev) { startVertexDrag(ev, i, vi); });
                    h.addEventListener('contextmenu', function(ev) { ev.preventDefault(); removeVertex(i, vi); });
                    svg.appendChild(h);
                });
            }
        });
        if (draft) {
            if (draft.length >= 2) {
                svg.appendChild(el('polyline', {
                    points: pointsAttr(draft), fill: 'none', stroke: '#5ad17a',
                    'stroke-width': 2, 'stroke-dasharray': '6 5', 'vector-effect': 'non-scaling-stroke'
                }));
            }
            draft.forEach(function(v, vi) {
                svg.appendChild(el('circle', {
                    cx: v[0] * imgW, cy: v[1] * imgH, r: vi === 0 ? 8 : 6,
                    fill: vi === 0 ? '#ffcc55' : '#5ad17a', 'vector-effect': 'non-scaling-stroke'
                }));
            });
        }
    }
    function renderList() {
        listEl.innerHTML = '';
        masks.forEach(function(m, i) {
            var li = document.createElement('li');
            li.className = 'dme-row' + (i === selected ? ' dme-selected' : '');
            var chk = document.createElement('input');
            chk.type = 'checkbox'; chk.checked = m.enabled; chk.title = 'Enabled';
            chk.addEventListener('change', function() {
                m.enabled = chk.checked; updateMask(i, { enabled: m.enabled }); render();
            });
            var name = document.createElement('input');
            name.type = 'text'; name.value = m.name; name.placeholder = 'Mask ' + (i + 1);
            name.addEventListener('focus', function() { select(i); });
            name.addEventListener('blur', function() {
                if (name.value !== m.name) { m.name = name.value; updateMask(i, { name: m.name }); }
            });
            var count = document.createElement('span');
            count.className = 'dme-count'; count.textContent = m.vertices.length + ' pts';
            var del = document.createElement('button');
            del.type = 'button'; del.textContent = 'Delete';
            del.addEventListener('click', function() { deleteMask(i); });
            li.appendChild(chk); li.appendChild(name); li.appendChild(count); li.appendChild(del);
            li.addEventListener('click', function(ev) { if (ev.target === li) select(i); });
            listEl.appendChild(li);
        });
    }
    function setHint(t) { hintEl.textContent = t || ''; }
    function updateToolbar() {
        var drawing = !!draft;
        // Toggle a class, not the ``hidden`` attribute: the global ``button``
        // rule sets ``display``, which (author origin) defeats the UA
        // ``[hidden]`` rule – so a hidden button stays visible and clickable.
        btnNew.classList.toggle('dme-hidden', drawing);
        btnFinish.classList.toggle('dme-hidden', !drawing);
        btnCancel.classList.toggle('dme-hidden', !drawing);
        btnDelete.disabled = drawing || selected < 0;
    }
    function select(i) {
        if (draft) return;
        selected = i; updateToolbar(); render(); renderList();
    }

    // -- persistence (JSON CRUD) --
    function jsonFetch(method, path, body) {
        return fetch(path, {
            method: method,
            headers: body ? { 'Content-Type': 'application/json' } : {},
            body: body ? JSON.stringify(body) : undefined
        });
    }
    function setMasterNote() {
        if (!enabledNote) return;
        enabledNote.textContent = (chkEnabled && chkEnabled.checked)
            ? 'Masks on – detection is limited to the enabled regions below.'
            : 'Masks off – detection runs over the whole frame.';
    }
    function setMasterEnabledUI(on) {
        if (chkEnabled) chkEnabled.checked = !!on;
        setMasterNote();
    }
    function saveMasterEnabled() {
        setMasterNote();
        jsonFetch('POST', '/api/detection/masks/enabled', { enabled: !!(chkEnabled && chkEnabled.checked) })
            .catch(function() {});
    }
    function fetchMasks() {
        return fetch('/api/detection/masks')
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(d) {
                if (!d || !Array.isArray(d.masks)) return;
                masks = d.masks.map(function(m) {
                    return {
                        name: m.name || '',
                        vertices: (m.vertices || []).map(function(v) { return [v[0], v[1]]; }),
                        enabled: m.enabled !== false
                    };
                });
                setMasterEnabledUI(d.masks_enabled === true);
                if (selected >= masks.length) selected = masks.length - 1;
                updateToolbar(); render(); renderList();
            })
            .catch(function() {});
    }
    function createMask(vertices) {
        return jsonFetch('POST', '/api/detection/masks', { name: 'Mask ' + (masks.length + 1), vertices: vertices, enabled: true })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(d) { if (d && typeof d.index === 'number') selected = d.index; return fetchMasks(); });
    }
    function updateMask(i, patch) { return jsonFetch('PUT', '/api/detection/masks/' + i, patch).catch(function() {}); }
    function deleteMask(i) {
        return jsonFetch('DELETE', '/api/detection/masks/' + i)
            .then(function() { selected = -1; return fetchMasks(); }).catch(function() {});
    }

    // -- drawing flow --
    function startDraft() { draft = []; selected = -1; updateToolbar(); render(); renderList(); setHint('Click to add points. Click the first point or double-click to finish.'); }
    function cancelDraft() { draft = null; updateToolbar(); render(); setHint(''); }
    function finishDraft() {
        if (!draft) return;
        if (draft.length < 3) { setHint('A mask needs at least 3 points.'); return; }
        var verts = draft; draft = null; updateToolbar(); setHint('');
        createMask(verts);
    }
    function onStageClick(evt) {
        if (!draft) return;
        var norm = clientToNorm(evt);
        if (!norm) return;
        if (draft.length >= 3 && screenDist(draft[0], evt) <= CLOSE_PX) { finishDraft(); return; }
        if (draft.length && screenDist(draft[draft.length - 1], evt) <= DEDUP_PX) return;
        draft.push(norm); render();
    }

    // -- vertex drag --
    function startVertexDrag(evt, maskIndex, vertexIndex) {
        if (draft) return;
        evt.preventDefault();
        select(maskIndex);
        drag = { mask: maskIndex, vertex: vertexIndex };
        window.addEventListener('pointermove', onVertexMove);
        window.addEventListener('pointerup', onVertexUp);
    }
    function onVertexMove(evt) {
        if (!drag) return;
        var norm = clientToNorm(evt);
        if (!norm) return;
        masks[drag.mask].vertices[drag.vertex] = norm;
        render();
    }
    function onVertexUp() {
        if (!drag) return;
        var i = drag.mask;
        drag = null;
        window.removeEventListener('pointermove', onVertexMove);
        window.removeEventListener('pointerup', onVertexUp);
        updateMask(i, { vertices: masks[i].vertices });
        renderList();
    }
    function removeVertex(maskIndex, vertexIndex) {
        var m = masks[maskIndex];
        if (m.vertices.length <= 3) { setHint('A mask needs at least 3 points.'); return; }
        m.vertices.splice(vertexIndex, 1);
        render(); renderList();
        updateMask(maskIndex, { vertices: m.vertices });
    }

    // -- snapshot --
    function setFeed(ok) {
        // Toggle a class, not an inline ``display`` – an inline style would beat
        // the section-collapse stylesheet rule and keep the canvas visible while
        // the box is collapsed.
        stage.classList.toggle('dme-hidden', !ok);
        noFeed.hidden = ok;
        btnNew.disabled = !ok;
    }
    function loadSnapshot() {
        fetch('/api/video/snapshot/full')
            .then(function(r) { if (!r.ok) throw new Error('no feed'); return r.blob(); })
            .then(function(blob) {
                var prev = snapshotUrl;
                var url = URL.createObjectURL(blob);
                var im = new Image();
                im.onload = function() {
                    imgW = im.naturalWidth || 1280;
                    imgH = im.naturalHeight || 720;
                    snapshotUrl = url;
                    img.src = url;
                    svg.setAttribute('viewBox', '0 0 ' + imgW + ' ' + imgH);
                    setFeed(true);
                    render();
                    if (prev) { try { URL.revokeObjectURL(prev); } catch (e) {} }
                };
                im.onerror = function() { try { URL.revokeObjectURL(url); } catch (e) {} setFeed(false); };
                im.src = url;
            })
            .catch(function() { setFeed(false); });
    }

    // -- wire up --
    btnNew.addEventListener('click', startDraft);
    btnFinish.addEventListener('click', finishDraft);
    btnCancel.addEventListener('click', cancelDraft);
    btnDelete.addEventListener('click', function() { if (selected >= 0) deleteMask(selected); });
    btnRefresh.addEventListener('click', loadSnapshot);
    if (chkEnabled) chkEnabled.addEventListener('change', saveMasterEnabled);
    svg.addEventListener('click', onStageClick);
    svg.addEventListener('dblclick', function(ev) { if (draft) { ev.preventDefault(); finishDraft(); } });

    updateToolbar();
    setMasterNote();
    fetchMasks();
    loadSnapshot();
})();
</script>
