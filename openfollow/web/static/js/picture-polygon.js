// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
//
// One polygon's corners on a PictureView, handled as the Setup Wizard handles its
// points: named pins, a drag with the loupe, a press that does not move only
// chooses, keys on the focused picture, and a drag from an edge adds a corner.
// The caller owns the polygon and maps picture points to the stage and back.
(function () {
    'use strict';

    // How long after the last arrow step the corner is saved.
    var KEY_SAVE_MS = 400;
    // Width of the band along an edge that a press adds a corner from, in CSS pixels.
    var EDGE_BAND = 20;

    // Shift+Tab is down: focus arriving now came from after a picture. One pair of listeners for every picture.
    var backTab = false;
    document.addEventListener('keydown', function (ev) { backTab = ev.key === 'Tab' && ev.shiftKey; }, true);
    document.addEventListener('keyup', function () { backTab = false; }, true);

    function nearestOn(samples, p) {
        var best = null, bd = Infinity;
        for (var i = 0; i + 1 < samples.length; i++) {
            var a = samples[i], b = samples[i + 1];
            if (!a || !b) continue;
            var dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy;
            var t = len ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len)) : 0;
            var q = [a[0] + dx * t, a[1] + dy * t], d = Math.hypot(q[0] - p[0], q[1] - p[1]);
            if (d < bd) { bd = d; best = q; }
        }
        return best;
    }

    // opts (each a function): corners() the chosen polygon's corners as picture points (null where the camera does
    // not see one), or null without a polygon; edge(i) picture points along the edge from corner i to the next;
    // place(i, at), insert(i, at) (a corner after corner i) and remove(i), each true when done; snapshot() and
    // restore(snap) to put a cancelled drag back; readout(i); changed() redraws, calling draw(); save(); press(ev)
    // for a press off the corners. ``min`` is the fewest corners a polygon keeps. Esc with nothing chosen is left
    // to the page.
    function PicturePolygon(view, opts) {
        this.view = view;
        this.opts = opts;
        this.min = opts.min || 3;
        this.chosen = null;
        this.keyboard = false;   // the chosen corner was reached by key: it shows the focus ring
        this._kind = 'mouse';    // what chose the corner, which sets how far off it the loupe sits
        this._drag = null;
        this._soon = 0;          // the pending key save's token; a newer step or a flush replaces it
        this._waiting = false;
        this._bind();
    }

    var P = PicturePolygon.prototype;

    // True while a drag or a key edit is under way, when a reload would undo the corner in hand.
    P.busy = function () { return !!this._drag || this._waiting; };

    P.choose = function (i, keyboard) {
        this.flush();
        this.chosen = i;
        this.keyboard = !!keyboard && i !== null;
    };

    P.canDelete = function () {
        var corners = this.opts.corners();
        return !this._drag && this.chosen !== null && !!corners && corners.length > this.min;
    };

    // One save for the corner gone, with any key edit still waiting in it. The nearest corner the camera sees,
    // the next one first, is chosen in its place.
    P.deleteChosen = function () {
        if (!this.canDelete() || !this.opts.remove(this.chosen)) return;
        this._waiting = false;
        this._soon++;
        var corners = this.opts.corners(), at = Math.min(this.chosen, corners.length - 1), next = null;
        for (var d = 0; d < corners.length && next === null; d++) {
            if (this.view.inPicture(corners[at - d])) next = at - d;
            else if (this.view.inPicture(corners[at + d])) next = at + d;
        }
        this.chosen = next;
        this.opts.changed();
        this.opts.save();
    };

    // Ends a drag without keeping it: the corner goes back, a corner the drag added goes, nothing is saved.
    P.cancelDrag = function () {
        var d = this._drag;
        if (!d) return;
        this._drag = null;
        if (this.view.svg.releasePointerCapture) this.view.svg.releasePointerCapture(d.id);
        if (d.moved) this.opts.restore(d.before);
        if (d.edge !== undefined) this.chosen = null;
        this.opts.changed();
    };

    // Saves a key edit still waiting for its moment.
    P.flush = function () {
        if (!this._waiting) return;
        this._soon++;
        this._saved();
    };

    P._saveSoon = function () {
        var self = this, token = ++this._soon;
        this._waiting = true;
        setTimeout(function () { if (token === self._soon) self._saved(); }, KEY_SAVE_MS);
    };

    // A key edit is saved: its readout is read out once.
    P._saved = function () {
        this._waiting = false;
        if (this.chosen !== null) this.view.announce(this.opts.readout(this.chosen));
        this.opts.save();
    };

    // The edge bands, the hover dot and the pins, over whatever the caller drew, and the loupe on the chosen corner
    // while the camera sees it, as the Setup Wizard keeps it on a held point.
    P.draw = function () {
        var view = this.view, svg = view.svg, corners = this.opts.corners();
        svg.removeAttribute('aria-activedescendant');
        if (!corners) { this.chosen = null; view.hideLoupe(); return; }
        if (this.chosen !== null && this.chosen >= corners.length) this.chosen = null;
        var k = view.cssScale(), self = this;
        corners.forEach(function (_, i) {
            view.polylines(self.opts.edge(i)).forEach(function (run) {
                var band = view.el('polyline', { points: run, 'class': 'pv-edge', 'stroke-width': EDGE_BAND * k });
                band.dataset.edge = i;
                svg.appendChild(band);
            });
        });
        this._ghost = view.el('circle', { r: 7 * k, 'class': 'pv-ghost' });
        this._ghost.style.display = 'none';
        svg.appendChild(this._ghost);
        corners.forEach(function (pt, i) {
            if (!view.inPicture(pt)) return;
            var pin = view.pin(pt, 'V' + (i + 1), i === self.chosen, i === self.chosen && self.keyboard);
            pin.dataset.corner = i;
            if (svg.id) pin.setAttribute('id', svg.id + '-v' + (i + 1));
            svg.appendChild(pin);
        });
        var held = this.chosen !== null && corners[this.chosen];
        if (!view.inPicture(held)) { view.hideLoupe(); return; }
        if (svg.id) svg.setAttribute('aria-activedescendant', svg.id + '-v' + (this.chosen + 1));
        view.showLoupe(held, this._kind, this.opts.readout(this.chosen));
    };

    P._at = function (ev) {
        var at = this.view.toPicture(ev);
        if (!at) return null;
        return [Math.max(0, Math.min(this.view.w, at.x)), Math.max(0, Math.min(this.view.h, at.y))];
    };

    P._hover = function (ev) {
        var ghost = this._ghost;
        if (!ghost) return;
        var band = !this._drag && ev.pointerType === 'mouse' && ev.target && ev.target.closest && ev.target.closest('.pv-edge');
        var at = band && this._at(ev), q = at && nearestOn(this.opts.edge(+band.dataset.edge), at);
        if (!q) { ghost.style.display = 'none'; return; }
        ghost.setAttribute('cx', q[0]);
        ghost.setAttribute('cy', q[1]);
        ghost.style.display = '';
    };

    P._press = function (ev) {
        var target = ev.target && ev.target.closest ? ev.target : null;
        var pin = target && target.closest('.pv-pin'), band = !pin && target && target.closest('.pv-edge');
        // One corner at a time: another pointer waits for the drag to end. Only the main button presses.
        if (this._drag) { ev.preventDefault(); return; }
        if (ev.button > 0) return;
        this.flush();
        this._kind = ev.pointerType;
        if (!pin && !band) {
            this.choose(null);
            this.opts.press(ev);
            this.opts.changed();
            return;
        }
        ev.preventDefault();
        var slop = window.OpenFollow.PictureView.tapSlop(ev.pointerType);
        var drag = { i: null, start: { x: ev.clientX, y: ev.clientY, slop: slop, type: ev.pointerType },
            moved: false, id: ev.pointerId, before: this.opts.snapshot() };
        if (pin) {
            this.choose(+pin.dataset.corner);
            drag.i = this.chosen;
        } else {
            drag.edge = +band.dataset.edge;
        }
        this._drag = drag;
        this.view.svg.setPointerCapture(ev.pointerId);
        this.view.svg.focus({ preventScroll: true });
        this.opts.changed();
    };

    P._move = function (ev) {
        var d = this._drag;
        if (!d || ev.pointerId !== d.id) { this._hover(ev); return; }
        // The button was let go where the page did not see it.
        if (ev.buttons === 0) { this._end(ev); return; }
        if (!d.moved && Math.hypot(ev.clientX - d.start.x, ev.clientY - d.start.y) < d.start.slop) return;
        var at = this._at(ev);
        if (!at) return;
        if (d.i === null) {
            if (!this.opts.insert(d.edge, at)) return;
            d.i = this.chosen = d.edge + 1;
            this.keyboard = false;
        } else if (!this.opts.place(d.i, at)) return;
        d.moved = true;
        this.opts.changed();
    };

    // A let-go drag keeps its corner, and so does one the browser takes away (a cancel, a lost capture).
    P._end = function (ev) {
        var d = this._drag;
        if (!d || ev.pointerId !== d.id) return;
        this._drag = null;
        this.opts.changed();
        if (!d.moved) return;
        this.view.announce(this.opts.readout(d.i));
        this.opts.save();
    };

    P._key = function (ev) {
        if (this._drag) {
            if (ev.key === 'Escape') { this.cancelDrag(); ev.preventDefault(); }
            return;
        }
        var corners = this.opts.corners();
        if (ev.key === 'Escape') {
            if (this.chosen !== null) { this.choose(null); this.opts.changed(); ev.preventDefault(); }
            return;
        }
        if (!corners) return;
        if (ev.key === 'Tab') {
            this._kind = 'mouse';
            if (this.chosen === null && ev.shiftKey) return;
            var dir = ev.shiftKey ? -1 : 1, next = this.chosen === null ? 0 : this.chosen + dir;
            while (next >= 0 && next < corners.length && !this.view.inPicture(corners[next])) next += dir;
            if (next < 0 || next >= corners.length) { this.choose(null); this.opts.changed(); return; }
            this.choose(next, true);
            this.opts.changed();
            ev.preventDefault();
            return;
        }
        if (this.chosen === null) return;
        if (ev.key === 'Delete' || ev.key === 'Backspace') { this.deleteChosen(); ev.preventDefault(); return; }
        var step = ev.shiftKey ? 10 : 1;
        var d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[ev.key];
        if (!d) return;
        ev.preventDefault();
        var p = corners[this.chosen], at = p && [p[0] + d[0], p[1] + d[1]];
        if (!this.view.inPicture(at) || !this.opts.place(this.chosen, at)) return;
        this.keyboard = true;
        this._kind = 'mouse';
        this.opts.changed();
        this._saveSoon();
    };

    P._bind = function () {
        var self = this, svg = this.view.svg;
        svg.addEventListener('pointerdown', function (ev) { self._press(ev); });
        svg.addEventListener('pointermove', function (ev) { self._move(ev); });
        svg.addEventListener('pointerup', function (ev) { self._end(ev); });
        svg.addEventListener('pointercancel', function (ev) { self._end(ev); });
        svg.addEventListener('lostpointercapture', function (ev) { self._end(ev); });
        // A right click cancels a mouse drag; a long press on a touch screen or with a pen keeps its drag.
        svg.addEventListener('contextmenu', function (ev) {
            if (!self._drag) return;
            if (self._drag.start.type === 'mouse') self.cancelDrag();
            else ev.preventDefault();
        });
        svg.addEventListener('pointerleave', function () { if (!self._drag) self._hover({ target: null }); });
        svg.addEventListener('keydown', function (ev) { self._key(ev); });
        svg.addEventListener('blur', function () { self.flush(); });
        // Shift+Tab into the picture enters at its last corner the camera sees.
        svg.addEventListener('focus', function () {
            var corners = self.opts.corners();
            if (!backTab || self.chosen !== null || !corners) return;
            for (var i = corners.length - 1; i >= 0; i--) {
                if (self.view.inPicture(corners[i])) {
                    self._kind = 'mouse';
                    self.choose(i, true);
                    self.opts.changed();
                    return;
                }
            }
        });
    };

    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.PicturePolygon = PicturePolygon;
})();
