// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
//
// The camera picture with an SVG drawn over it, in picture pixels: the latest
// snapshot, what the saved camera sees of world points (/api/scene/project) and
// the stage point under a picture point (/api/scene/unproject). Handles drag in
// the picture and land on a horizontal plane the caller names. Named pins and the
// Setup Wizard's loupe serve editors that move points in the browser (picture-polygon.js).
(function () {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';

    // A run of visible picture points per polyline; a point behind the camera (null) breaks the line.
    function runs(points) {
        var out = [], run = [];
        points.forEach(function (pt) {
            if (pt) run.push(pt);
            else { if (run.length > 1) out.push(run); run = []; }
        });
        if (run.length > 1) out.push(run);
        return out;
    }

    // Resolves the station's JSON answer; rejects with saveError's {error, action, href}.
    function api(method, url, body) {
        var saveError = window.OpenFollow.saveError;
        var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        return fetch(url, opts).then(function (res) {
            if (!res.ok) return saveError.fromResponse(res).then(function (info) { throw info; });
            return res.json().catch(function () { return {}; });
        }, function () { throw saveError.UNREACHABLE; });
    }

    // opts: stage (holds img and svg), img, svg, nofeed, onSize() after a picture of a new size loads.
    function PictureView(opts) {
        this.stage = opts.stage;
        this.img = opts.img;
        this.svg = opts.svg;
        this.nofeed = opts.nofeed;
        this.onSize = opts.onSize || function () {};
        this.w = 1280;
        this.h = 720;
        this._url = null;
        this._drag = null;      // {drop, dot} while a handle is held
        this._dragEnded = false; // the click that ends a drag is not a click on the picture
        this._bind();
    }

    var P = PictureView.prototype;

    P.load = function () {
        var view = this;
        return fetch('/api/video/snapshot/full', { cache: 'no-store' }).then(function (res) {
            if (!res.ok) throw new Error('no feed');
            return res.blob();
        }).then(function (blob) {
            var url = URL.createObjectURL(blob);
            view.img.onload = function () {
                if (view._url) URL.revokeObjectURL(view._url);
                view._url = url;
                view.w = view.img.naturalWidth || view.w;
                view.h = view.img.naturalHeight || view.h;
                view.svg.setAttribute('viewBox', '0 0 ' + view.w + ' ' + view.h);
                view.stage.hidden = false;
                view.nofeed.hidden = true;
                view.onSize();
            };
            view.img.src = url;
        }).catch(function () {
            view.stage.hidden = true;
            view.nofeed.hidden = false;
        });
    };

    P.el = function (name, attrs) {
        var n = document.createElementNS(SVG_NS, name);
        Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
        return n;
    };

    P.clear = function () {
        while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild);
    };

    // SVG ``points`` strings, one per visible run.
    P.polylines = function (points) {
        return runs(points).map(function (run) { return run.map(function (pt) { return pt.join(','); }).join(' '); });
    };

    P.toPicture = function (evt) {
        var ctm = this.svg.getScreenCTM();
        if (!ctm) return null;
        var pt = this.svg.createSVGPoint();
        pt.x = evt.clientX; pt.y = evt.clientY;
        var at = pt.matrixTransform(ctm.inverse());
        return { x: at.x, y: at.y };
    };

    // The stage point under picture point ``at`` on the plane at height ``z``.
    P.onStage = function (at, z) {
        return api('POST', '/api/scene/unproject', { x: at.x, y: at.y, z: z, w: this.w, h: this.h });
    };

    // World polylines and single points, each [x, y, z], as picture points: {polylines, points}.
    P.project = function (polylines, points) {
        return api('POST', '/api/scene/project', { w: this.w, h: this.h, polylines: polylines, points: points || [] });
    };

    // A drag handle at picture point ``pt``. ``onGrab`` runs on press and returns the
    // drop callback, which gets the picture point the handle was let go at; null refuses the drag.
    P.handle = function (pt, cls, onGrab) {
        var view = this;
        var dot = this.el('circle', { cx: pt[0], cy: pt[1], r: 7, 'class': cls, 'vector-effect': 'non-scaling-stroke' });
        dot.style.cursor = 'grab';
        dot.addEventListener('pointerdown', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var drop = onGrab();
            if (!drop) return;
            view._drag = { drop: drop, dot: dot };
            view.svg.setPointerCapture(ev.pointerId);
        });
        return dot;
    };

    // Picture pixels per CSS pixel, so a handle sized in CSS pixels is one size on a phone and on a 4K screen.
    P.cssScale = function () {
        var box = this.svg.getBoundingClientRect();
        return box && box.width > 0 && box.height > 0 ? Math.max(this.w / box.width, this.h / box.height) : 1;
    };

    P.inPicture = function (pt) {
        return !!pt && pt[0] >= 0 && pt[0] <= this.w && pt[1] >= 0 && pt[1] <= this.h;
    };

    // A named point in the Setup Wizard's pin style around a 44 CSS px target; ``ring`` marks the keyboard's point.
    P.pin = function (pt, label, active, ring) {
        var k = this.cssScale(), flip = pt[0] > this.w - 70 * k;
        var g = this.el('g', { 'class': 'pv-pin' + (active ? ' active' : ''), transform: 'translate(' + pt[0] + ',' + pt[1] + ')',
            role: 'img', 'aria-label': label });
        g.appendChild(this.el('rect', { x: -22 * k, y: -22 * k, width: 44 * k, height: 44 * k, fill: 'transparent' }));
        if (ring) g.appendChild(this.el('circle', { r: 15 * k, 'class': 'pv-pin-ring' }));
        g.appendChild(this.el('circle', { r: 9 * k, 'class': 'pv-pin-dot' }));
        g.appendChild(this.el('path', { d: 'M' + (-5 * k) + ' 0H' + 5 * k + 'M0 ' + (-5 * k) + 'V' + 5 * k, 'class': 'pv-pin-cross' }));
        var text = this.el('text', { x: (flip ? -14 : 14) * k, y: -12 * k, 'class': 'pv-pin-label', 'font-size': 13 * k,
            'stroke-width': 3 * k, 'text-anchor': flip ? 'end' : 'start' });
        text.textContent = label;
        g.appendChild(text);
        return g;
    };

    var LOUPE = 120, ZOOM = 4;

    // The loupe's top left, its edge ``gap`` from the point (x, y): up-right first, then the other corners, inside the box.
    function loupeSpot(x, y, gap, width, height) {
        var rad = LOUPE / 2, k = (rad + gap) / Math.SQRT2;
        var spots = [[1, -1], [-1, -1], [-1, 1], [1, 1]].map(function (d) { return [x + d[0] * k - rad, y + d[1] * k - rad]; });
        var at = spots.filter(function (q) { return q[0] >= 0 && q[1] >= 0 && q[0] + LOUPE <= width && q[1] + LOUPE <= height; })[0] || spots[0];
        return [Math.max(0, Math.min(at[0], width - LOUPE)), Math.max(0, Math.min(at[1], height - LOUPE))];
    }

    // The wizard's loupe beside picture point ``pt`` (close to a mouse, a finger's width from a touch), ``text`` under it.
    P.showLoupe = function (pt, pointerType, text) {
        if (!this._loupe) {
            this._loupe = document.createElement('div');
            this._loupe.className = 'pv-loupe';
            this._loupe.setAttribute('aria-hidden', 'true');
            this._loupe.innerHTML = '<div class="pv-loupe-img"></div><svg viewBox="0 0 120 120">'
                + '<path class="pv-loupe-shade" d="M60 40V80M40 60H80"/><path class="pv-loupe-cross" d="M60 40V80M40 60H80"/></svg>';
            this._readout = document.createElement('div');
            this._readout.className = 'pv-readout';
            this._readout.setAttribute('aria-live', 'polite');
            this.stage.appendChild(this._loupe);
            this.stage.appendChild(this._readout);
        }
        var box = this.svg.getBoundingClientRect(), k = this.cssScale(), x = pt[0] / k, y = pt[1] / k;
        var at = loupeSpot(x, y, pointerType === 'mouse' ? 12 : 30, box.width, box.height);
        this._loupe.hidden = false;
        this._loupe.style.left = at[0] + 'px';
        this._loupe.style.top = at[1] + 'px';
        var img = this._loupe.firstChild;
        if (this._url) img.style.backgroundImage = 'url(' + this._url + ')';
        img.style.backgroundSize = (this.w / k) * ZOOM + 'px ' + (this.h / k) * ZOOM + 'px';
        img.style.backgroundPosition = (LOUPE / 2 - x * ZOOM) + 'px ' + (LOUPE / 2 - y * ZOOM) + 'px';
        this._readout.hidden = false;
        this._readout.textContent = text;
        this._readout.style.left = at[0] + 'px';
        // Under the loupe, or over it where the picture ends.
        this._readout.style.top = (at[1] + LOUPE + 30 > box.height ? at[1] - 30 : at[1] + LOUPE + 6) + 'px';
    };

    P.hideLoupe = function () {
        if (!this._loupe) return;
        this._loupe.hidden = true;
        this._readout.hidden = true;
    };

    // True for the click that ends a drag.
    P.dragJustEnded = function () { return this._dragEnded; };

    P.dragging = function () { return !!this._drag; };

    P._bind = function () {
        var view = this, svg = this.svg;
        svg.addEventListener('pointerdown', function () { view._dragEnded = false; });
        // The held handle follows the pointer; the drop lands it on the stage.
        svg.addEventListener('pointermove', function (ev) {
            var at = view._drag && view.toPicture(ev);
            if (!at) return;
            view._drag.dot.setAttribute('cx', at.x);
            view._drag.dot.setAttribute('cy', at.y);
        });
        svg.addEventListener('pointerup', function (ev) {
            if (!view._drag) return;
            var drop = view._drag.drop;
            view._drag = null;
            view._dragEnded = true;
            var at = view.toPicture(ev);
            if (at) drop(at);
        });
        svg.addEventListener('pointercancel', function () { view._drag = null; });
    };

    // A Top-down / Camera radio pair: ``apply(mode)`` now and on every change; the
    // choice is remembered in this browser under ``key``.
    function viewSwitch(group, key, apply) {
        var storageKey = 'openfollow:plan-view:' + key;
        var mode = 'plan';
        try { if (localStorage.getItem(storageKey) === 'camera') mode = 'camera'; } catch (e) { /* no storage */ }
        group.querySelectorAll('input[type="radio"]').forEach(function (radio) {
            radio.checked = radio.value === mode;
            radio.addEventListener('change', function () {
                if (!radio.checked) return;
                try { localStorage.setItem(storageKey, radio.value); } catch (e) { /* no storage */ }
                apply(radio.value);
            });
        });
        apply(mode);
    }

    window.OpenFollow = window.OpenFollow || {};
    // CSS pixels a press may move and still be a click: a finger or a pen wanders more than a mouse.
    PictureView.tapSlop = function (pointerType) { return pointerType === 'mouse' ? 3 : 8; };
    window.OpenFollow.PictureView = PictureView;
    window.OpenFollow.viewSwitch = viewSwitch;
    window.OpenFollow.pictureRuns = runs;
    window.OpenFollow.stationApi = api;
})();
