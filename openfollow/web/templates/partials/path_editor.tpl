<div id="path-editor-section" class="section experimental-feature" data-fold-key="path_editor" data-help="path_editor">
    <div class="section-head">
        <h2>Path Editor <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">Each path's points and options, over the camera picture</span>
    </div>

    <div class="pe-list" data-pe="list"></div>
    <p class="empty-state" data-pe="empty" hidden>No paths yet. Use <em>+ New path</em> below to create one.</p>

    <div class="actions pe-toolbar">
        <button type="button" class="save-btn" data-pe="new">+ New path</button>
        <button type="button" class="secondary" data-pe="template-save">Save as template…</button>
        <button type="button" class="secondary" data-pe="template-load">Load template…</button>
        <button type="button" class="secondary" data-pe="push">Apply to all stations</button>
    </div>

    <div class="pe-stage" data-pe="stage">
        <img data-pe="img" alt="Camera picture with the paths">
        <svg data-pe="svg" viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"></svg>
    </div>
    <div class="pe-no-feed" data-pe="nofeed" hidden>No video picture.</div>
    <div class="actions">
        <button type="button" class="secondary" data-pe="refresh">Refresh Image</button>
    </div>
    <p class="pe-status" data-pe="status" aria-live="polite"></p>
</div>

<style>
    #path-editor-section.is-collapsed > :not(.section-head) { display: none; }
    #path-editor-section .path-row[open] > .path-body { margin-top: 0.9rem; }
    #path-editor-section .path-row.pe-selected { border-color: var(--accent); }
    #path-editor-section .pe-points input[type="text"] { width: 7em; }
    #path-editor-section .pe-points td button { padding: 2px 8px; }
    #path-editor-section .pe-add { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-top: 0.6rem; }
    #path-editor-section .pe-add select, #path-editor-section .pe-points select { width: auto; min-width: 10em; }
    #path-editor-section .pe-radius { display: inline-flex; align-items: center; gap: 0.4rem; white-space: nowrap; }
    #path-editor-section .pe-stage { position: relative; width: 100%; line-height: 0; border-radius: 8px; overflow: hidden; background: var(--bg-base); margin-top: 1rem; }
    #path-editor-section .pe-stage img { display: block; width: 100%; height: auto; }
    #path-editor-section .pe-stage svg { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: crosshair; }
    #path-editor-section .pe-no-feed { padding: 24px 12px; border: 1px dashed var(--border); border-radius: 8px; text-align: center; margin-top: 1rem; }
    #path-editor-section .pe-status { min-height: 1.2em; }
    #path-editor-section .pe-line-inactive { stroke: var(--muted); stroke-width: 1.5; stroke-dasharray: 6 5; fill: none; }
    #path-editor-section .pe-line-active { stroke: var(--text); stroke-width: 2; fill: none; }
    #path-editor-section .pe-line-selected { stroke: var(--accent); stroke-width: 3; fill: none; }
    #path-editor-section .pe-dot-inactive { fill: var(--muted); }
    #path-editor-section .pe-dot-active { fill: var(--text); }
    #path-editor-section .pe-dot-selected { fill: var(--accent); }
    #path-editor-section .pe-handle { fill: var(--text); stroke: var(--accent); stroke-width: 2; }
    #path-editor-section .pe-marker { fill: var(--accent); stroke: var(--bg-base); stroke-width: 1.5; }
    #path-editor-section .pe-marker-label { fill: var(--text); font-size: 14px; paint-order: stroke; stroke: var(--bg-base); stroke-width: 3; }
</style>

<script>
// A run of visible picture points per polyline; a point behind the camera (null) breaks the line.
function pePolylines(points) {
    var runs = [], run = [];
    points.forEach(function(pt) {
        if (pt) run.push(pt);
        else { if (run.length > 1) runs.push(run); run = []; }
    });
    if (run.length > 1) runs.push(run);
    return runs;
}
// The Operator Screen's arrowhead: a triangle centred on (x, y), its tip pointing along the unit (ux, uy).
function peArrowhead(x, y, ux, uy, half, side) {
    return [[x + ux * half, y + uy * half], [x - ux * half - uy * side, y - uy * half + ux * side], [x - ux * half + uy * side, y - uy * half - ux * side]];
}
(function() {
    var root = document.getElementById('path-editor-section');
    if (!root || root.dataset.peReady) return;
    root.dataset.peReady = '1';

    function q(name) { return root.querySelector('[data-pe="' + name + '"]'); }
    var units = window.OpenFollow.units;
    var SVG_NS = 'http://www.w3.org/2000/svg';
    var svg = q('svg'), img = q('img');
    var data = null;          // GET /api/paths
    var view = null;          // POST /api/paths/view for the picture shown
    var selected = null;      // the path the picture edits: the row opened last
    var open = {};            // name -> the row's open tab, for every open row
    var imgW = 1280, imgH = 720, snapshotUrl = null;
    var drag = null;          // {index, z} while a point is dragged
    var dragEnded = false;    // the click that ends a drag is not a click on the picture

    function api(method, url, body) {
        var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        return fetch(url, opts).then(function(res) {
            return res.json().catch(function() { return {}; }).then(function(json) {
                if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
                return json;
            });
        });
    }
    function say(text) { q('status').textContent = text || ''; }
    function failed(err) { say(err.message); }
    function enc(name) { return encodeURIComponent(name); }
    function find(name) {
        return data ? data.paths.find(function(p) { return p.name === name; }) || null : null;
    }
    function make(tag, attrs, children) {
        var n = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function(k) {
            if (k === 'text') n.textContent = attrs[k];
            else if (k === 'class') n.className = attrs[k];
            else n.setAttribute(k, attrs[k]);
        });
        (children || []).forEach(function(c) { n.append(c); });
        return n;
    }
    function button(text, onClick, cls) {
        var b = make('button', { type: 'button', 'class': cls || 'secondary', text: text });
        b.addEventListener('click', onClick);
        return b;
    }
    function checkbox(id, checked, label, onChange) {
        var input = make('input', { type: 'checkbox', id: id });
        input.checked = checked;
        input.addEventListener('change', function() { onChange(input.checked); });
        return make('div', { 'class': 'checkbox-wrap checkbox-field inline' }, [input, make('label', { 'for': id, text: label })]);
    }
    function field(label, forId, control, cls) {
        return make('div', { 'class': 'field' + (cls ? ' ' + cls : '') }, [make('label', { 'for': forId, text: label }), control]);
    }
    function lengthInput(value, onChange) {
        var input = make('input', { type: 'text', value: units.formatLength(value) });
        input.addEventListener('change', function() {
            var parsed = units.parseLength(input.value);
            input.setAttribute('aria-invalid', isFinite(parsed) ? 'false' : 'true');
            if (isFinite(parsed)) onChange(parsed);
        });
        return input;
    }

    // -- loading --------------------------------------------------------
    function load() {
        return api('GET', '/api/paths').then(function(json) {
            data = json;
            Object.keys(open).forEach(function(name) { if (!find(name)) delete open[name]; });
            if (selected !== null && !find(selected)) selected = null;
            renderList();
            return loadView();
        }).catch(failed);
    }
    function loadView() {
        return api('POST', '/api/paths/view', { w: imgW, h: imgH }).then(function(json) {
            view = json;
            renderFrame();
        }).catch(failed);
    }
    function loadSnapshot() {
        fetch('/api/video/snapshot/full', { cache: 'no-store' }).then(function(res) {
            if (!res.ok) throw new Error('no feed');
            return res.blob();
        }).then(function(blob) {
            var url = URL.createObjectURL(blob);
            img.onload = function() {
                if (snapshotUrl) URL.revokeObjectURL(snapshotUrl);
                snapshotUrl = url;
                imgW = img.naturalWidth || imgW;
                imgH = img.naturalHeight || imgH;
                svg.setAttribute('viewBox', '0 0 ' + imgW + ' ' + imgH);
                q('stage').hidden = false;
                q('nofeed').hidden = true;
                loadView();
            };
            img.src = url;
        }).catch(function() {
            q('stage').hidden = true;
            q('nofeed').hidden = false;
        });
    }

    // -- one row per path -------------------------------------------------
    function renderList() {
        var list = q('list');
        list.innerHTML = '';
        q('empty').hidden = data.paths.length > 0;
        var locks = {};
        Object.keys(data.locks).forEach(function(id) {
            var key = data.locks[id].toLowerCase();
            (locks[key] = locks[key] || []).push(id);
        });
        data.paths.forEach(function(p, index) {
            list.appendChild(row(p, index, locks[p.name.toLowerCase()] || []));
        });
    }
    function row(p, index, lockedIds) {
        var recording = !!data.recording && data.recording.toLowerCase() === p.name.toLowerCase();
        var details = make('details', { 'class': 'osc-binding-row path-row' + (p.name === selected ? ' pe-selected' : ''), 'data-path': p.name });
        var summary = make('summary', { 'class': 'osc-binding-summary' }, [
            make('span', { 'class': 'osc-binding-enabled-dot ' + (p.active ? 'on' : 'off'), 'aria-label': p.active ? 'Active' : 'Inactive' }),
            make('span', { 'class': 'osc-binding-title', text: p.name }),
            make('span', { 'class': 'osc-binding-kind-badge', text: p.closed ? 'Closed' : 'Open' }),
            make('span', { 'class': 'osc-binding-kind-badge', text: p.points.length + (p.points.length === 1 ? ' point' : ' points') })
        ]);
        if (recording) summary.appendChild(make('span', { 'class': 'stat-chip info', text: 'Recording' }));
        summary.appendChild(make('span', { 'class': 'osc-binding-target', text: lockedIds.length ? 'Marker ' + lockedIds.join(', ') : '' }));
        details.appendChild(summary);
        var tab = open[p.name] || 'basics';
        var key = 'path-' + index;
        var body = make('div', { 'class': 'path-body' });
        var bar = make('div', { 'class': 'row-tab-bar', role: 'tablist' });
        [['basics', 'Basics'], ['points', 'Points']].forEach(function(t) {
            var b = make('button', { type: 'button', 'class': 'row-tab-btn' + (tab === t[0] ? ' active' : ''), 'data-row-tab': key + '-' + t[0], role: 'tab', 'aria-selected': tab === t[0] ? 'true' : 'false', 'aria-controls': 'row-tab-' + key + '-' + t[0], text: t[1] });
            b.addEventListener('click', function() { open[p.name] = t[0]; });
            bar.appendChild(b);
        });
        body.appendChild(bar);
        body.appendChild(basicsPanel(p, key, tab === 'basics'));
        body.appendChild(pointsPanel(p, key, tab === 'points'));
        body.appendChild(make('div', { 'class': 'actions osc-binding-actions' }, [
            button(recording ? 'Stop recording' : 'Record', function() {
                api('POST', '/api/paths/' + enc(p.name) + '/record', { on: !recording }).then(load, failed);
            }),
            button('Delete', function() {
                if (!window.confirm('Delete path ' + p.name + '?')) return;
                api('DELETE', '/api/paths/' + enc(p.name)).then(load, failed);
            }, 'danger')
        ]));
        details.appendChild(body);
        if (p.name in open) details.open = true;
        details.addEventListener('toggle', function() {
            if (details.open) {
                if (!(p.name in open)) open[p.name] = 'basics';
                selected = p.name;
            } else {
                delete open[p.name];
                if (selected === p.name) selected = null;
            }
            root.querySelectorAll('.path-row').forEach(function(r) { r.classList.toggle('pe-selected', r.dataset.path === selected); });
            renderFrame();
        });
        return details;
    }
    function put(p, change) {
        return api('PUT', '/api/paths/' + enc(p.name), change);
    }
    function basicsPanel(p, key, active) {
        var panel = make('div', { 'class': 'row-tab-panel' + (active ? ' active' : ''), id: 'row-tab-' + key + '-basics', role: 'tabpanel' });
        var name = make('input', { type: 'text', id: key + '-name', value: p.name, maxlength: '32', autocomplete: 'off', spellcheck: 'false' });
        name.addEventListener('change', function() {
            put(p, { name: name.value }).then(function(json) {
                var tab = open[p.name];
                delete open[p.name];
                open[json.path.name] = tab;
                if (selected === p.name) selected = json.path.name;
                return load();
            }).catch(function(err) { name.value = p.name; failed(err); });
        });
        var first = make('div', { 'class': 'row' }, [
            make('div', { 'class': 'field checkbox-field' }, [
                make('label', { 'for': key + '-active', text: 'Path' }),
                checkbox(key + '-active', p.active, 'Let markers lock onto it', function(on) {
                    api('POST', '/api/paths/' + enc(p.name) + '/active', { active: on }).then(load, failed);
                })
            ]),
            field('Name', key + '-name', name)
        ]);
        var second = make('div', { 'class': 'row' }, [
            make('div', { 'class': 'field checkbox-field' }, [
                make('label', { 'for': key + '-closed', text: 'Shape' }),
                checkbox(key + '-closed', p.closed, 'Join the last point to the first', function(on) { put(p, { closed: on }).then(load, failed); })
            ])
        ]);
        panel.append(first, second);
        return panel;
    }
    // A corner's radius, or what an open path's end does with a marker pushed past it.
    function option(p, pt, i, last, key, save) {
        if (p.closed || (i > 0 && i < last)) {
            var id = key + '-radius-' + i;
            var radius = lengthInput(pt.radius, function(v) {
                var points = p.points.slice();
                points[i] = Object.assign({}, pt, { radius: v });
                save(points);
            });
            radius.id = id;
            return make('span', { 'class': 'pe-radius' }, [make('label', { 'for': id, text: 'Radius:' }), radius]);
        }
        if (last === 0) return '–';
        var flag = i === 0 ? 'release_at_start' : 'release_at_end';
        var end = make('select', { 'aria-label': 'End at point ' + (i + 1) });
        [['hold', 'Hold at end'], ['release', 'Release at end']].forEach(function(o) {
            end.appendChild(make('option', { value: o[0], text: o[1] }));
        });
        end.value = p[flag] ? 'release' : 'hold';
        end.addEventListener('change', function() {
            var change = {};
            change[flag] = end.value === 'release';
            put(p, change).then(load, failed);
        });
        return end;
    }
    function pointsPanel(p, key, active) {
        var panel = make('div', { 'class': 'row-tab-panel' + (active ? ' active' : ''), id: 'row-tab-' + key + '-points', role: 'tabpanel' });
        var save = function(points) { return put(p, { points: points }).then(load, failed); };
        var last = p.points.length - 1;
        var tbody = make('tbody');
        p.points.forEach(function(pt, i) {
            var cells = [String(i + 1)];
            ['x', 'y', 'z'].forEach(function(axis) {
                cells.push(lengthInput(pt[axis], function(v) {
                    var points = p.points.slice();
                    points[i] = Object.assign({}, pt);
                    points[i][axis] = v;
                    save(points);
                }));
            });
            cells.push(option(p, pt, i, last, key, save));
            var move = function(step) {
                var j = i + step;
                if (j < 0 || j > last) return;
                var points = p.points.slice();
                var t = points[i]; points[i] = points[j]; points[j] = t;
                save(points);
            };
            cells.push(make('span', {}, [
                button('↑', function() { move(-1); }),
                ' ',
                button('↓', function() { move(1); }),
                ' ',
                button('Delete', function() {
                    var points = p.points.slice();
                    points.splice(i, 1);
                    save(points);
                }, 'danger')
            ]));
            tbody.appendChild(make('tr', {}, cells.map(function(c) { return make('td', {}, [c]); })));
        });
        var head = make('thead', {}, [make('tr', {}, ['#', 'X', 'Y', 'Z', 'Option', ''].map(function(t) { return make('th', { text: t }); }))]);
        if (p.points.length) panel.appendChild(make('table', { 'class': 'data-table pe-points', 'aria-label': 'Points of ' + p.name }, [head, tbody]));
        else panel.appendChild(make('p', { 'class': 'empty-state', text: 'No points yet.' }));
        var addPoint = function(pt) {
            if (p.points.length >= data.max_points) { say('A path holds at most ' + data.max_points + ' points.'); return; }
            save(p.points.concat([{ x: pt.x, y: pt.y, z: pt.z, radius: 0.5 }]));
        };
        var markerSel = make('select', { id: key + '-marker' });
        data.markers.forEach(function(id) { markerSel.appendChild(make('option', { value: String(id), text: 'Marker ' + id })); });
        panel.appendChild(make('div', { 'class': 'pe-add' }, [
            button('+ Point', function() {
                var lastPt = p.points[last];
                addPoint(lastPt ? { x: lastPt.x + 1, y: lastPt.y, z: lastPt.z } : { x: 0, y: 0, z: data.floor_z });
            }),
            make('label', { 'class': 'inline-label', 'for': key + '-marker', text: 'Point from' }),
            markerSel,
            button('Add', function() {
                if (!markerSel.value) return;
                api('GET', '/api/paths/marker/' + markerSel.value).then(addPoint, failed);
            })
        ]));
        return panel;
    }

    // -- the picture ------------------------------------------------------
    function el(name, attrs) {
        var n = document.createElementNS(SVG_NS, name);
        Object.keys(attrs).forEach(function(k) { n.setAttribute(k, attrs[k]); });
        return n;
    }
    function polylines(points) {
        return pePolylines(points).map(function(run) { return run.map(function(pt) { return pt.join(','); }).join(' '); });
    }
    function renderFrame() {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        if (!view || !data) return;
        view.paths.forEach(function(v) {
            var p = find(v.name);
            var isSel = v.name === selected;
            var state = isSel ? 'selected' : (p && p.active ? 'active' : 'inactive');
            polylines(v.line).forEach(function(points) {
                svg.appendChild(el('polyline', { points: points, 'class': 'pe-line-' + state, 'vector-effect': 'non-scaling-stroke' }));
            });
            // An end that holds is a dot, one that releases an arrowhead pointing out; a corner is its curve.
            v.ends.forEach(function(end) {
                if (!end.at) return;
                var dx = end.inside ? end.at[0] - end.inside[0] : 0, dy = end.inside ? end.at[1] - end.inside[1] : 0;
                var len = Math.hypot(dx, dy);
                if (end.releases && len > 0) {
                    // Beyond the selected path's handle, so the handle does not hide it.
                    var shift = isSel ? 16 : 0;
                    svg.appendChild(arrowhead(end.at[0] + dx / len * shift, end.at[1] + dy / len * shift, dx / len, dy / len, 'pe-dot-' + state));
                } else if (!isSel) {
                    svg.appendChild(el('circle', { cx: end.at[0], cy: end.at[1], r: 4, 'class': 'pe-dot-' + state, 'vector-effect': 'non-scaling-stroke' }));
                }
            });
            if (!isSel) return;
            v.points.forEach(function(pt, i) {
                if (!pt) return;
                var dot = el('circle', { cx: pt[0], cy: pt[1], r: 7, 'class': 'pe-handle', 'vector-effect': 'non-scaling-stroke' });
                dot.style.cursor = 'grab';
                dot.addEventListener('pointerdown', function(ev) { startDrag(ev, i); });
                svg.appendChild(dot);
            });
        });
        view.markers.forEach(function(m) {
            if (!m.at) return;
            svg.appendChild(el('circle', { cx: m.at[0], cy: m.at[1], r: 5, 'class': 'pe-marker', 'vector-effect': 'non-scaling-stroke' }));
            var label = el('text', { x: m.at[0] + 8, y: m.at[1] - 8, 'class': 'pe-marker-label' });
            label.textContent = '#' + m.id;
            svg.appendChild(label);
        });
    }
    function arrowhead(x, y, ux, uy, cls) {
        // In picture pixels, the Operator Screen's 14 x 12 px arrowhead on a 1920 px wide picture.
        var pts = peArrowhead(x, y, ux, uy, 7 * imgW / 1920, 6 * imgW / 1920);
        return el('polygon', { points: pts.map(function(p) { return p.join(','); }).join(' '), 'class': cls });
    }
    function toPicture(evt) {
        var ctm = svg.getScreenCTM();
        if (!ctm) return null;
        var pt = svg.createSVGPoint();
        pt.x = evt.clientX; pt.y = evt.clientY;
        var at = pt.matrixTransform(ctm.inverse());
        return { x: at.x, y: at.y };
    }
    function onStage(at, z) {
        return api('POST', '/api/paths/unproject', { x: at.x, y: at.y, z: z, w: imgW, h: imgH });
    }
    function startDrag(ev, index) {
        ev.preventDefault();
        ev.stopPropagation();
        drag = { index: index, z: find(selected).points[index].z };
        svg.setPointerCapture(ev.pointerId);
    }
    svg.addEventListener('pointerdown', function() { dragEnded = false; });
    svg.addEventListener('pointerup', function(ev) {
        if (!drag) return;
        var at = toPicture(ev);
        var d = drag;
        drag = null;
        dragEnded = true;
        if (!at) return;
        var p = find(selected);
        onStage(at, d.z).then(function(w) {
            var points = p.points.slice();
            points[d.index] = Object.assign({}, points[d.index], { x: w.x, y: w.y });
            return put(p, { points: points }).then(load);
        }).catch(failed);
    });
    svg.addEventListener('pointercancel', function() { drag = null; });
    svg.addEventListener('click', function(ev) {
        if (dragEnded) return;
        var p = find(selected);
        if (drag || !p) return;
        var at = toPicture(ev);
        if (!at) return;
        onStage(at, data.floor_z).then(function(w) {
            if (p.points.length >= data.max_points) { say('A path holds at most ' + data.max_points + ' points.'); return; }
            return put(p, { points: p.points.concat([{ x: w.x, y: w.y, z: w.z, radius: 0.5 }]) }).then(load);
        }).catch(failed);
    });

    // -- toolbar --------------------------------------------------------
    q('new').addEventListener('click', function() {
        api('POST', '/api/paths', {}).then(function(json) {
            open[json.name] = 'basics';
            selected = json.name;
            return load();
        }, failed);
    });
    q('refresh').addEventListener('click', loadSnapshot);
    q('template-save').addEventListener('click', function() {
        window.saveCurrentSectionAsTemplate({ type: 'paths', title: 'Save paths as template', placeholder: 'e.g. Stage crosses', onSaved: function() {} });
    });
    q('template-load').addEventListener('click', function() {
        window.modalChooseTemplate({
            type: 'paths',
            title: 'Load paths template',
            applyConfirmMessage: 'Loading a paths template replaces every path on this station. Continue?',
            onApplied: load,
        });
    });
    q('push').addEventListener('click', function() {
        api('GET', '/api/config/paths').then(function(section) {
            return api('POST', '/api/config/paths/broadcast', section);
        }).then(function(json) {
            var failedPeers = json.peer_results.filter(function(peer) { return !peer.success; });
            say(failedPeers.length ? 'Not applied on ' + failedPeers.length + ' station(s).' : 'Applied to ' + json.peer_results.length + ' station(s).');
        }).catch(failed);
    });

    // Recording adds points from the Operator Screen; keep the rows in step while it runs.
    setInterval(function() {
        if (data && data.recording && root.offsetParent !== null && !root.contains(document.activeElement)) load();
    }, 1000);

    load();
    loadSnapshot();
})();
</script>
