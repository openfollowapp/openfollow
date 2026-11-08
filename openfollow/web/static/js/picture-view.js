// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
//
// The camera picture with an SVG drawn over it, in picture pixels: the latest
// snapshot, what the saved camera sees of world points (/api/scene/project) and
// the stage point under a picture point (/api/scene/unproject). Handles drag in
// the picture and land on a horizontal plane the caller names.
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
    window.OpenFollow.PictureView = PictureView;
    window.OpenFollow.viewSwitch = viewSwitch;
    window.OpenFollow.pictureRuns = runs;
    window.OpenFollow.stationApi = api;
})();
