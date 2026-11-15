// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
// Copyright (C) 2026 Michel Honold and Paul Hermann
//
// The page around the Path Editor in tests/test_paths_editor_js.py: what base.tpl gives
// every partial script, a scripted station behind /api/paths, and helpers for the steps.

// -- base.tpl, recorded --------------------------------------------------------
var confirmed = [], confirmAnswer = true, toasts = [];
function modalConfirm(opts) { confirmed.push(opts); return Promise.resolve(confirmAnswer); }
function showToast(message) { toasts.push(message); }
window.saveCurrentSectionAsTemplate = function () {};
var templateDialog = null;
window.modalChooseTemplate = function (opts) { templateDialog = opts; };
window.DOMParser = function () {};
window.DOMParser.prototype.parseFromString = function (text) {
    return { querySelector: function () { return null; }, body: { textContent: text } };
};

// -- the station: two paths; the picture shows stage (x, y) at pixel (640 + 100 x, 360 + 100 y)
function clone(v) { return JSON.parse(JSON.stringify(v)); }
function blank(name, extra) {
    return Object.assign({ name: name, active: false, closed: false, release_at_start: false, release_at_end: false, points: [] }, extra);
}
var model = {
    paths: [
        blank('cross', { active: true, points: [{ x: -3, y: 2, z: 0, radius: 0.5 }, { x: 3, y: 2, z: 0, radius: 0.5 }] }),
        blank('loop', { points: [{ x: -1, y: 4, z: 0, radius: 0.5 }, { x: 1, y: 4, z: 0, radius: 0.5 }] }),
    ],
    recording: null, locks: {}, markers: [2],
};
var refuse = null;   // (request) -> an answer that replaces the station's, or nothing
function seen(pt) { return [640 + 100 * pt.x, 360 + 100 * pt.y]; }
function findPath(raw) {
    var name = decodeURIComponent(raw).toLowerCase();
    return model.paths.find(function (p) { return p.name.toLowerCase() === name; });
}
station = function (req) {
    var out = refuse && refuse(req);
    if (out) return out;
    var m, url = req.url, b = req.body;
    if (url === '/api/paths' && req.method === 'GET') {
        return { body: Object.assign({ show_paths: true, slide_time: 1, floor_z: 0, max_paths: 32, max_points: 100 }, clone(model)) };
    }
    if (url === '/api/paths' && req.method === 'POST') {
        var name = 'path-' + (model.paths.length + 1);
        model.paths.push(blank(name));
        return { body: { success: true, name: name } };
    }
    if (url === '/api/paths/view') {
        return { body: { markers: [], paths: model.paths.map(function (p) {
            return { name: p.name, line: p.points.map(seen), points: p.points.map(seen), ends: [] };
        }) } };
    }
    if (url === '/api/scene/unproject') return { body: { x: (b.x - 640) / 100, y: (b.y - 360) / 100, z: b.z } };
    if ((m = /^\/api\/paths\/marker\/(\d+)$/.exec(url))) return { body: { x: 1, y: 1, z: 1.5 } };
    if ((m = /^\/api\/paths\/([^/]+)(\/\w+)?$/.exec(url))) {
        var p = findPath(m[1]);
        if (!p) return { status: 404, body: { error: 'No path called ' + m[1] + '.' } };
        if (m[2] === '/active') { p.active = b.active; return { body: { success: true } }; }
        if (m[2] === '/record') { model.recording = b.on ? p.name : null; return { body: { success: true } }; }
        if (req.method === 'DELETE') { model.paths.splice(model.paths.indexOf(p), 1); return { body: { success: true } }; }
        if ('name' in b) p.name = b.name.replace(/[^A-Za-z0-9._-]/g, '');
        ['closed', 'release_at_start', 'release_at_end'].forEach(function (k) { if (k in b) p[k] = b[k]; });
        if (b.points) p.points = clone(b.points);
        return { body: { success: true, path: clone(p) } };
    }
    return { status: 503, body: 'no feed' };
};

// -- helpers for the steps -------------------------------------------------------
function editor() { return document.getElementById('path-editor-section'); }
function picture() { return editor().querySelector('svg'); }
// A path's control by what it is: 'name', 'closed', 'x-0', 'delete-2', 'add-point', 'record'...
function control(name, what) { return document.getElementById('pe:' + name + ':' + what); }
function row(name) { return editor().querySelector('.path-row[data-path="' + name + '"]'); }
function openRow(name) { row(name).querySelector('summary').click(); }
function puts() { return requests.filter(function (r) { return r.method === 'PUT'; }); }
function handles() { return editor().querySelectorAll('.pe-handle'); }
function pointer(target, type, x, y) { target.dispatchEvent(makeEvent(type, { clientX: x, clientY: y, pointerId: 1 })); }
function selectedRow() { var r = editor().querySelector('.path-row.pe-selected'); return r ? r.dataset.path : null; }
function inputs(name, i) { return row(name).querySelectorAll('.pe-points tbody tr')[i].querySelectorAll('input'); }
function errorLine() { var line = editor().querySelector('.save-error'); return line ? line.textContent : null; }
function pointRows(name) { return row(name).querySelectorAll('.pe-points tbody tr'); }
// Drag point ``from`` of a path by its handle onto point ``onto``, in its upper (y 5) or lower (y 15) half.
function dragPoint(name, from, onto, y) {
    var handle = pointRows(name)[from].querySelector('.row-drag-handle'), target = pointRows(name)[onto];
    var ev = function (type) { return makeEvent(type, { clientY: y, dataTransfer: { setData: function () {} } }); };
    handle.dispatchEvent(ev('dragstart'));
    target.dispatchEvent(ev('dragover'));
    target.dispatchEvent(ev('drop'));
    handle.dispatchEvent(ev('dragend'));
}
function points(i) { return model.paths[i].points.map(function (p) { return [p.x, p.y]; }); }
function sent(pattern) { return requests.filter(function (r) { return pattern.test(r.url); }).map(function (r) { return r.body; }); }
function asked(pattern) { return requests.filter(function (r) { return pattern.test(r.url); }).map(function (r) { return r.url; }); }
function texts(nodes) { return nodes.map(function (n) { return n.textContent; }); }
function labels(name, controls) { return controls.map(function (c) { return control(name, c).getAttribute('aria-label'); }); }
function twoPoints(y) { return [{ x: 0, y: y, z: 0, radius: 0.5 }, { x: 1, y: y, z: 0, radius: 0.5 }]; }
