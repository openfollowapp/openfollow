<div id="path-editor-section" class="section experimental-feature" data-fold-key="path_editor" data-help="path_editor">
    <div class="section-head">
        <h2>Path Editor <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">Each path's points and options, over the camera picture</span>
    </div>

    <div class="pe-list" data-pe="list"></div>
    <p class="empty-state" data-pe="empty" hidden>No paths yet. Use <em>+ New path</em> below to create one.</p>

    <div class="actions pe-toolbar" data-pe="toolbar">
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
</div>

<style>
    #path-editor-section.is-collapsed > :not(.section-head) { display: none; }
    #path-editor-section .path-row[open] > .path-body { margin-top: 0.9rem; }
    #path-editor-section .path-row.pe-selected { border-color: var(--accent); }
    #path-editor-section .pe-points input[type="text"] { width: 7em; }
    #path-editor-section .pe-points td button { padding: 2px 8px; }
    #path-editor-section .pe-points td:first-child { width: 1rem; }
    #path-editor-section .pe-add { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-top: 0.6rem; }
    #path-editor-section .pe-add select, #path-editor-section .pe-points select { width: auto; min-width: 10em; }
    #path-editor-section .pe-radius { display: inline-flex; align-items: center; gap: 0.4rem; white-space: nowrap; }
    #path-editor-section .pe-stage { position: relative; width: 100%; line-height: 0; border-radius: 8px; overflow: hidden; background: var(--bg-base); margin-top: 1rem; }
    #path-editor-section .pe-stage img { display: block; width: 100%; height: auto; }
    #path-editor-section .pe-stage svg { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: crosshair; }
    #path-editor-section .pe-no-feed { padding: 24px 12px; border: 1px dashed var(--border); border-radius: 8px; text-align: center; margin-top: 1rem; }
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
    var saveError = window.OpenFollow.saveError;
    var svg = q('svg'), toolbar = q('toolbar');
    var pv = new window.OpenFollow.PictureView({
        stage: q('stage'), img: q('img'), svg: svg, nofeed: q('nofeed'), onSize: loadView,
    });
    var data = null;          // GET /api/paths, with the edits made here since
    var view = null;          // POST /api/paths/view for the picture shown
    var views = 0;            // view requests made, so an older answer is dropped
    var viewAt = null;        // the edit count the shown view was asked at: no handles on a picture behind the rows
    var selected = null;      // the path the picture edits: the row opened last
    var open = {};            // name -> the row's open tab, for every open row
    var pointFrom = null;     // the marker "Point from" adds, kept across redraws
    var focusNext = null;     // the control an action sends focus to at the next redraw
    var waiting = [];         // writes not sent yet, oldest first; one is in flight at a time
    var writing = false;
    var edits = 0;            // a load asked for before the latest edit is out of date
    var removing = false;     // Chrome fires change and blur on a focused field a redraw removes

    // Resolves the station's JSON answer; rejects with saveError's {error, action, href}.
    function api(method, url, body) {
        var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        return fetch(url, opts).then(function(res) {
            if (!res.ok) return saveError.fromResponse(res).then(function(info) { throw info; });
            return res.json().catch(function() { return {}; });
        }, function() { throw saveError.UNREACHABLE; });
    }
    function failed(info, lead) { saveError.show(root, info, lead, toolbar); }
    function ignore() {}
    function enc(name) { return encodeURIComponent(name); }
    function find(name) {
        return data ? data.paths.find(function(p) { return p.name === name; }) || null : null;
    }
    // Every control's id names its path, so a redraw puts focus back in the same path's control.
    function idOf(name, control) { return 'pe:' + name + ':' + control; }
    function recordingInto(name) { return !!data.recording && data.recording.toLowerCase() === name.toLowerCase(); }
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
    function button(text, onClick, cls, attrs) {
        var b = make('button', Object.assign({ type: 'button', 'class': cls || 'secondary', text: text }, attrs));
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
    // Commits on change, or when left holding text it has not committed; defaultValue is the text committed last.
    function textInput(attrs, onCommit) {
        var input = make('input', Object.assign({ type: 'text' }, attrs));
        function commit() {
            if (removing || input.value === input.defaultValue) return;
            if (onCommit(input.value) !== false) input.defaultValue = input.value;
        }
        input.addEventListener('change', commit);
        input.addEventListener('blur', commit);
        return input;
    }
    function lengthInput(id, value, label, onChange) {
        var input = textInput({ id: id, value: units.formatLength(value), 'aria-label': label }, function(text) {
            var parsed = units.parseLength(text);
            input.setAttribute('aria-invalid', isFinite(parsed) ? 'false' : 'true');
            if (!isFinite(parsed)) {
                failed({ error: label + ': "' + text + '" is not a length.', action: '', href: '' });
                return false;
            }
            onChange(parsed);
        });
        return input;
    }

    // -- loading --------------------------------------------------------
    function load() {
        var asked = edits;
        return api('GET', '/api/paths').then(function(json) {
            if (asked !== edits || writing) return;
            var changed = JSON.stringify(json) !== JSON.stringify(data);
            data = json;
            Object.keys(open).forEach(function(name) { if (!find(name)) delete open[name]; });
            if (selected !== null && !find(selected)) selected = null;
            if (changed) {
                viewAt = null;
                renderList();
                renderFrame();
            }
            return loadView();
        }).catch(ignore);
    }
    function loadView() {
        var asked = ++views, at = edits;
        return api('POST', '/api/paths/view', { w: pv.w, h: pv.h }).then(function(json) {
            if (asked !== views) return;
            view = json;
            viewAt = at;
            renderFrame();
        }).catch(ignore);
    }

    // -- saving: edits apply here at once, then go to the station in order ---
    // One write in flight at a time. A write still waiting under the same key sends the latest data when it goes.
    function write(key, send) {
        edits++;
        saveError.clear(root);
        renderFrame();
        if (!key || !waiting.some(function(w) { return w.key === key; })) waiting.push({ key: key, send: send });
        if (!writing) next();
    }
    function next() {
        var w = waiting.shift();
        writing = !!w;
        if (w) w.send().catch(failed).then(next);
        else load();
    }
    // Changes one field of the path called ``name`` here and saves that field alone; ``redraw`` when the rows change.
    function edit(name, field, change, redraw) {
        var p = find(name);
        if (!p) return;
        change(p);
        if (redraw) renderList();
        write('path:' + p.name + ':' + field, function() {
            var body = {};
            body[field] = p[field];
            return api('PUT', '/api/paths/' + enc(p.name), body);
        });
    }
    // While a path records, its points are the Operator Screen's to add to.
    function editPoints(name, change, redraw) {
        if (recordingInto(name)) {
            failed({ error: 'Path ' + name + ' is recording.', action: 'Stop recording to change its points.', href: '' });
            return;
        }
        edit(name, 'points', change, redraw);
    }
    function addPoint(name, pt) {
        var p = find(name);
        if (!p) return;
        if (p.points.length >= data.max_points) {
            failed({ error: 'A path holds at most ' + data.max_points + ' points.', action: '', href: '' });
            return;
        }
        editPoints(name, function(path) { path.points.push({ x: pt.x, y: pt.y, z: pt.z, radius: 0.5 }); }, true);
    }
    function rename(name, to) {
        var p = find(name);
        if (!p) return;
        write(null, function() {
            return api('PUT', '/api/paths/' + enc(p.name), { name: to }).then(function(json) {
                var from = p.name;
                p.name = json.path.name;
                if (from !== p.name && from in open) { open[p.name] = open[from]; delete open[from]; }
                if (selected === from) selected = p.name;
                renderList(from, p.name);
            }, function(err) {
                renderList();
                throw err;
            });
        });
    }
    function setActive(name, on) {
        var p = find(name);
        if (!p) return;
        p.active = on;
        renderList();
        write('active:' + p.name, function() {
            return api('POST', '/api/paths/' + enc(p.name) + '/active', { active: p.active });
        });
    }

    // -- one row per path -------------------------------------------------
    // Rebuilds the rows, then focuses the control the operator was in, in the same path (renamed ``from`` to ``to``),
    // with what was typed there and not yet saved; an action can name another control in ``focusNext``.
    function renderList(from, to) {
        var list = q('list');
        var keep = document.activeElement, want = focusNext || keep.id;
        focusNext = null;
        if (from && want.indexOf(idOf(from, '')) === 0) want = idOf(to, want.slice(idOf(from, '').length));
        removing = true;
        list.innerHTML = '';
        removing = false;
        q('empty').hidden = data.paths.length > 0;
        var locks = {};
        Object.keys(data.locks).forEach(function(id) {
            var key = data.locks[id].toLowerCase();
            (locks[key] = locks[key] || []).push(id);
        });
        data.paths.forEach(function(p) {
            list.appendChild(row(p, locks[p.name.toLowerCase()] || []));
        });
        var again = want ? document.getElementById(want) : null;
        if (!again) return;
        again.focus();
        if (again.type !== 'text') return;
        if (keep.value !== keep.defaultValue) {
            again.value = keep.value;
            again.setAttribute('aria-invalid', keep.getAttribute('aria-invalid') || 'false');
        }
        // Tab selects a field's text, so typing replaces it; a redraw before the first key must keep that.
        again.setSelectionRange(keep.selectionStart, keep.selectionEnd);
    }
    function row(p, lockedIds) {
        var name = p.name;
        var recording = recordingInto(name);
        var details = make('details', { 'class': 'osc-binding-row path-row' + (name === selected ? ' pe-selected' : ''), 'data-path': name });
        var summary = make('summary', { 'class': 'osc-binding-summary', id: idOf(name, 'summary') }, [
            make('span', { 'class': 'osc-binding-enabled-dot ' + (p.active ? 'on' : 'off'), 'aria-label': p.active ? 'Active' : 'Inactive' }),
            make('span', { 'class': 'osc-binding-title', text: name }),
            make('span', { 'class': 'osc-binding-kind-badge', text: p.closed ? 'Closed' : 'Open' }),
            make('span', { 'class': 'osc-binding-kind-badge', text: p.points.length + (p.points.length === 1 ? ' point' : ' points') })
        ]);
        if (recording) summary.appendChild(make('span', { 'class': 'stat-chip info', text: 'Recording' }));
        summary.appendChild(make('span', { 'class': 'osc-binding-target', text: lockedIds.length ? 'Marker ' + lockedIds.join(', ') : '' }));
        details.appendChild(summary);
        var tab = open[name] || 'basics';
        var body = make('div', { 'class': 'path-body' });
        var bar = make('div', { 'class': 'row-tab-bar', role: 'tablist' });
        [['basics', 'Basics'], ['points', 'Points']].forEach(function(t) {
            var b = make('button', { type: 'button', id: idOf(name, 'tab-' + t[0]), 'class': 'row-tab-btn' + (tab === t[0] ? ' active' : ''), 'data-row-tab': idOf(name, t[0]), role: 'tab', 'aria-selected': tab === t[0] ? 'true' : 'false', 'aria-controls': 'row-tab-' + idOf(name, t[0]), text: t[1] });
            b.addEventListener('click', function() { open[name] = t[0]; });
            bar.appendChild(b);
        });
        body.appendChild(bar);
        body.appendChild(basicsPanel(p, tab === 'basics'));
        body.appendChild(pointsPanel(p, tab === 'points'));
        body.appendChild(make('div', { 'class': 'actions osc-binding-actions' }, [
            button(recording ? 'Stop recording' : 'Record', function() {
                var path = find(name);
                write(null, function() { return api('POST', '/api/paths/' + enc(path.name) + '/record', { on: !recording }); });
            }, 'secondary', { id: idOf(name, 'record') }),
            button('Delete', function() {
                var path = find(name);
                modalConfirm({ title: 'Delete path?', message: 'Delete path ' + name + '?', confirmLabel: 'Delete', danger: true }).then(function(ok) {
                    if (ok) write(null, function() { return api('DELETE', '/api/paths/' + enc(path.name)); });
                });
            }, 'danger', { id: idOf(name, 'delete') })
        ]));
        details.appendChild(body);
        if (name in open) details.open = true;
        // The operator's click, not the toggle event: a redraw reopening a row fires toggle too.
        summary.addEventListener('click', function() {
            if (details.open) {
                delete open[name];
                if (selected === name) selected = null;
            } else {
                open[name] = 'basics';
                selected = name;
            }
            root.querySelectorAll('.path-row').forEach(function(r) { r.classList.toggle('pe-selected', r.dataset.path === selected); });
            renderFrame();
        });
        return details;
    }
    function basicsPanel(p, active) {
        var name = p.name;
        var panel = make('div', { 'class': 'row-tab-panel' + (active ? ' active' : ''), id: 'row-tab-' + idOf(name, 'basics'), role: 'tabpanel' });
        var nameInput = textInput({ id: idOf(name, 'name'), value: name, maxlength: '32', autocomplete: 'off', spellcheck: 'false' }, function(text) {
            rename(name, text);
        });
        var first = make('div', { 'class': 'row' }, [
            make('div', { 'class': 'field checkbox-field' }, [
                make('label', { 'for': idOf(name, 'active'), text: 'Path' }),
                checkbox(idOf(name, 'active'), p.active, 'Let markers lock onto it', function(on) { setActive(name, on); })
            ]),
            field('Name', idOf(name, 'name'), nameInput)
        ]);
        var second = make('div', { 'class': 'row' }, [
            make('div', { 'class': 'field checkbox-field' }, [
                make('label', { 'for': idOf(name, 'closed'), text: 'Shape' }),
                checkbox(idOf(name, 'closed'), p.closed, 'Join the last point to the first', function(on) {
                    edit(name, 'closed', function(path) { path.closed = on; }, true);
                })
            ])
        ]);
        panel.append(first, second);
        return panel;
    }
    // A corner's radius, or what an open path's end does with a marker pushed past it.
    function option(p, pt, i, last, recording) {
        var name = p.name;
        if (p.closed || (i > 0 && i < last)) {
            var id = idOf(name, 'radius-' + i);
            var radius = lengthInput(id, pt.radius, 'Point ' + (i + 1) + ' radius', function(v) {
                editPoints(name, function(path) { path.points[i].radius = v; }, false);
            });
            radius.disabled = recording;
            return make('span', { 'class': 'pe-radius' }, [make('label', { 'for': id, text: 'Radius:' }), radius]);
        }
        if (last === 0) return '–';
        var flag = i === 0 ? 'release_at_start' : 'release_at_end';
        var end = make('select', { id: idOf(name, 'end-' + i), 'aria-label': 'End at point ' + (i + 1) });
        [['hold', 'Hold at end'], ['release', 'Release at end']].forEach(function(o) {
            end.appendChild(make('option', { value: o[0], text: o[1] }));
        });
        end.value = p[flag] ? 'release' : 'hold';
        end.addEventListener('change', function() {
            edit(name, flag, function(path) { path[flag] = end.value === 'release'; }, false);
        });
        return end;
    }
    function pointsPanel(p, active) {
        var name = p.name;
        var recording = recordingInto(name);
        var panel = make('div', { 'class': 'row-tab-panel' + (active ? ' active' : ''), id: 'row-tab-' + idOf(name, 'points'), role: 'tabpanel' });
        var last = p.points.length - 1;
        var tbody = make('tbody');
        // The shared drag-reorder in base.tpl moves the rows and reports their new order here.
        tbody.addEventListener('rows-reordered', function(ev) {
            var order = ev.detail.order.map(Number);
            editPoints(name, function(path) {
                var was = path.points;
                path.points = order.map(function(i) { return was[i]; });
            }, true);
        });
        p.points.forEach(function(pt, i) {
            var n = i + 1;
            var cells = [''];
            if (!recording) {
                var handle = make('span', { 'class': 'row-drag-handle', draggable: 'true', 'aria-hidden': 'true', title: 'Drag to reorder', text: '⋮⋮' });
                handle.addEventListener('pointerdown', function(ev) { ev.stopPropagation(); });
                handle.addEventListener('click', function(ev) { ev.preventDefault(); ev.stopPropagation(); });
                cells[0] = handle;
            }
            cells.push(String(n));
            ['x', 'y', 'z'].forEach(function(axis) {
                var input = lengthInput(idOf(name, axis + '-' + i), pt[axis], 'Point ' + n + ' ' + axis.toUpperCase(), function(v) {
                    editPoints(name, function(path) { path.points[i][axis] = v; }, false);
                });
                input.disabled = recording;
                cells.push(input);
            });
            cells.push(option(p, pt, i, last, recording));
            var remove = button('Delete', function() {
                editPoints(name, function(path) {
                    // Focus goes to the Delete of the point that takes this one's place.
                    focusNext = idOf(name, i < last ? 'delete-' + i : (last > 0 ? 'delete-' + (i - 1) : 'add-point'));
                    path.points.splice(i, 1);
                }, true);
            }, 'danger', { id: idOf(name, 'delete-' + i), 'aria-label': 'Delete point ' + n });
            remove.disabled = recording;
            cells.push(remove);
            tbody.appendChild(make('tr', { 'class': 'reorder-row', 'data-row-id': String(i) }, cells.map(function(c) { return make('td', {}, [c]); })));
        });
        var head = make('thead', {}, [make('tr', {}, ['', '#', 'X', 'Y', 'Z', 'Option', ''].map(function(t) { return make('th', { text: t }); }))]);
        if (p.points.length) panel.appendChild(make('table', { 'class': 'data-table pe-points', 'aria-label': 'Points of ' + name }, [head, tbody]));
        else panel.appendChild(make('p', { 'class': 'empty-state', text: 'No points yet.' }));
        var markerSel = make('select', { id: idOf(name, 'marker') });
        data.markers.forEach(function(id) { markerSel.appendChild(make('option', { value: String(id), text: 'Marker ' + id })); });
        if (data.markers.some(function(id) { return String(id) === pointFrom; })) markerSel.value = pointFrom;
        markerSel.addEventListener('change', function() { pointFrom = markerSel.value; });
        var addPointButton = button('+ Point', function() {
            var path = find(name), end = path && path.points[path.points.length - 1];
            addPoint(name, end ? { x: end.x + 1, y: end.y, z: end.z } : { x: 0, y: 0, z: data.floor_z });
        }, 'secondary', { id: idOf(name, 'add-point') });
        var addMarkerButton = button('Add', function() {
            if (!markerSel.value) return;
            api('GET', '/api/paths/marker/' + markerSel.value).then(function(pt) { addPoint(name, pt); }, failed);
        }, 'secondary', { id: idOf(name, 'add-marker') });
        addPointButton.disabled = addMarkerButton.disabled = recording;
        panel.appendChild(make('div', { 'class': 'pe-add' }, [
            addPointButton,
            make('label', { 'class': 'inline-label', 'for': idOf(name, 'marker'), text: 'Point from' }),
            markerSel,
            addMarkerButton
        ]));
        return panel;
    }

    // -- the picture ------------------------------------------------------
    function el(name, attrs) { return pv.el(name, attrs); }
    function renderFrame() {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        if (!view || !data) return;
        view.paths.forEach(function(v) {
            var p = find(v.name);
            var isSel = v.name === selected;
            var state = isSel ? 'selected' : (p && p.active ? 'active' : 'inactive');
            pv.polylines(v.line).forEach(function(points) {
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
            // Handles only on a picture that shows the path as the rows have it, and not while it records.
            if (!isSel || viewAt !== edits || recordingInto(v.name)) return;
            v.points.forEach(function(pt, i) {
                if (pt) svg.appendChild(pv.handle(pt, 'pe-handle', function() { return grab(i); }));
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
        var pts = peArrowhead(x, y, ux, uy, 7 * pv.w / 1920, 6 * pv.w / 1920);
        return el('polygon', { points: pts.map(function(p) { return p.join(','); }).join(' '), 'class': cls });
    }
    // Dropped where the picture shows it, on the plane at the point's own height.
    function grab(index) {
        var name = selected, pt = find(name).points[index];
        if (!pt) return null;
        var z = pt.z;
        return function(at) {
            pv.onStage(at, z).then(function(w) {
                editPoints(name, function(path) { Object.assign(path.points[index] || {}, { x: w.x, y: w.y }); }, true);
            }).catch(failed);
        };
    }
    svg.addEventListener('click', function(ev) {
        if (pv.dragJustEnded()) return;
        var p = find(selected);
        if (pv.dragging() || !p) return;
        var at = pv.toPicture(ev);
        if (!at) return;
        var name = p.name;
        pv.onStage(at, data.floor_z).then(function(w) { addPoint(name, w); }).catch(failed);
    });

    // -- toolbar --------------------------------------------------------
    q('new').addEventListener('click', function() {
        write(null, function() {
            return api('POST', '/api/paths', {}).then(function(json) {
                open[json.name] = 'basics';
                selected = json.name;
            });
        });
    });
    q('refresh').addEventListener('click', function() { pv.load(); });
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
        write(null, function() {
            return api('GET', '/api/config/paths').then(function(section) {
                return api('POST', '/api/config/paths/broadcast', section);
            }).then(function(json) {
                var peers = json.peer_results, missed = peers.filter(function(peer) { return !peer.success; });
                if (!missed.length) { showToast('Applied to ' + peers.length + ' station(s)'); return; }
                failed({
                    error: 'Not applied on ' + missed.length + ' of ' + peers.length + ' stations: ' + missed.map(function(peer) { return peer.name || peer.ip; }).join(', ') + '.',
                    action: 'Check that they are switched on, then apply again.',
                    href: ''
                }, '');
            }, function(info) { failed(info, 'Not applied.'); });
        });
    });

    // Recording adds points from the Operator Screen: follow it while the rows are on screen.
    function onScreen() { return !document.hidden && q('list').getClientRects().length > 0; }
    setInterval(function() {
        if (data && data.recording && !writing && onScreen()) load();
    }, 1000);

    load();
    pv.load();
})();
</script>
