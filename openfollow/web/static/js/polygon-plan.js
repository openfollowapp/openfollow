// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
//
// A top-down stage plan on a <canvas> for drawing and editing polygons in
// PSN-absolute world metres (X stage left, Y upstage): the main grid, the live
// markers, a polygon being drawn, and drag handles on one editable polygon.
// The page owns its polygons and draws them in ``opts.drawPolygons``.
(function () {
    'use strict';

    var PAD = 20;
    var HANDLE_HIT_SQ = 49;  // 7 px
    var CLOSE_HIT_SQ = 100;  // 10 px to the first corner closes a drawing

    function pointInPolygon(x, y, verts) {
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

    // opts:
    //   polygons()          every committed polygon, for fitting the view
    //   editable()          the vertex array that gets drag handles, or null
    //   drawPolygons(plan)  draws the page's polygons between grid and markers
    //   onPress(world)      a press that hit no handle (selection)
    //   onDragMove(index)   a handle moved; the array is already updated
    //   onDragEnd(index)    a drag ended
    //   onDraftClose(verts) a drawing closed with three or more corners
    //   onRender()          after every render (buttons, status)
    function PolygonPlan(canvas, opts) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.opts = opts || {};
        this.grid = {width: 10, depth: 10, spacing: 1, x_offset: 0, y_offset: 0};
        this.markers = [];
        this.draft = null;  // {vertices: [[x, y], ...]} while drawing
        this.drag = null;  // {index} while a handle is held
        // The view stays put while a handle is dragged, so the world point
        // under the pointer does not move as the polygon grows past the edge.
        this._frozenExtent = null;
        this._bind();
    }

    var P = PolygonPlan.prototype;

    // The visible world size: the grid plus every committed corner, one grid
    // spacing of padding, symmetric around the grid centre.
    P.extent = function () {
        if (this._frozenExtent) return this._frozenExtent;
        return this._liveExtent();
    };

    P._liveExtent = function () {
        var g = this.grid;
        var spacing = Math.max(0.1, g.spacing);
        var minX = g.x_offset - g.width / 2, maxX = g.x_offset + g.width / 2;
        var minY = g.y_offset - g.depth / 2, maxY = g.y_offset + g.depth / 2;
        var polys = this.opts.polygons ? this.opts.polygons() : [];
        for (var i = 0; i < polys.length; i++) {
            var verts = polys[i] || [];
            for (var j = 0; j < verts.length; j++) {
                var v = verts[j];
                if (v[0] < minX) minX = v[0];
                if (v[0] > maxX) maxX = v[0];
                if (v[1] < minY) minY = v[1];
                if (v[1] > maxY) maxY = v[1];
            }
        }
        minX -= spacing; maxX += spacing; minY -= spacing; maxY += spacing;
        var halfW = Math.max(g.x_offset - minX, maxX - g.x_offset);
        var halfD = Math.max(g.y_offset - minY, maxY - g.y_offset);
        return [Math.max(0.5, halfW * 2), Math.max(0.5, halfD * 2)];
    };

    P._scale = function () {
        var ext = this.extent();
        return Math.min((this.canvas.width - PAD * 2) / ext[0], (this.canvas.height - PAD * 2) / ext[1]);
    };

    // Upstage (+Y) is toward the top of the canvas.
    P.toScreen = function (wx, wy) {
        var s = this._scale();
        return [this.canvas.width / 2 + (wx - this.grid.x_offset) * s,
                this.canvas.height / 2 - (wy - this.grid.y_offset) * s];
    };

    P.toWorld = function (sx, sy) {
        var s = this._scale();
        return [(sx - this.canvas.width / 2) / s + this.grid.x_offset,
                -(sy - this.canvas.height / 2) / s + this.grid.y_offset];
    };

    P.pointer = function (evt) {
        var rect = this.canvas.getBoundingClientRect();
        return [(evt.clientX - rect.left) * (this.canvas.width / rect.width),
                (evt.clientY - rect.top) * (this.canvas.height / rect.height)];
    };

    P.vertexAt = function (sx, sy) {
        var verts = this.opts.editable ? this.opts.editable() : null;
        if (!verts) return null;
        for (var i = 0; i < verts.length; i++) {
            var p = this.toScreen(verts[i][0], verts[i][1]);
            var dx = p[0] - sx, dy = p[1] - sy;
            if (dx * dx + dy * dy <= HANDLE_HIT_SQ) return i;
        }
        return null;
    };

    // -- drawing --------------------------------------------------------

    P.render = function () {
        this._renderGrid();
        if (this.opts.drawPolygons) this.opts.drawPolygons(this);
        this._renderDraft();
        this._renderMarkers();
        if (this.opts.onRender) this.opts.onRender();
    };

    P.tracePolygon = function (verts) {
        var ctx = this.ctx;
        ctx.beginPath();
        for (var j = 0; j < verts.length; j++) {
            var p = this.toScreen(verts[j][0], verts[j][1]);
            if (j === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
        }
        if (verts.length >= 3) ctx.closePath();
    };

    // Round corner handles labelled V1, V2, ... to match a numeric corner list.
    P.drawHandles = function (verts, color) {
        var ctx = this.ctx;
        for (var k = 0; k < verts.length; k++) {
            var p = this.toScreen(verts[k][0], verts[k][1]);
            ctx.fillStyle = '#fff';
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.arc(p[0], p[1], 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            var label = 'V' + (k + 1);
            ctx.font = 'bold 11px sans-serif';
            ctx.textAlign = 'start';
            ctx.textBaseline = 'alphabetic';
            ctx.lineWidth = 3;
            ctx.strokeStyle = 'rgba(0,0,0,0.85)';
            ctx.strokeText(label, p[0] + 8, p[1] - 8);
            ctx.fillStyle = '#fff';
            ctx.fillText(label, p[0] + 8, p[1] - 8);
        }
    };

    P._renderGrid = function () {
        var ctx = this.ctx, g = this.grid;
        ctx.fillStyle = '#111';
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        var spacing = Math.max(0.1, g.spacing);
        var hw = g.width / 2, hd = g.depth / 2;
        ctx.strokeStyle = 'rgba(120,120,120,0.25)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        var a, b;
        for (var gx = -hw; gx <= hw + 1e-6; gx += spacing) {
            a = this.toScreen(g.x_offset + gx, g.y_offset - hd);
            b = this.toScreen(g.x_offset + gx, g.y_offset + hd);
            ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
        }
        for (var gy = -hd; gy <= hd + 1e-6; gy += spacing) {
            a = this.toScreen(g.x_offset - hw, g.y_offset + gy);
            b = this.toScreen(g.x_offset + hw, g.y_offset + gy);
            ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
        }
        ctx.stroke();
        ctx.strokeStyle = 'rgba(200,200,200,0.6)';
        ctx.lineWidth = 1.5;
        this.tracePolygon([
            [g.x_offset - hw, g.y_offset - hd], [g.x_offset + hw, g.y_offset - hd],
            [g.x_offset + hw, g.y_offset + hd], [g.x_offset - hw, g.y_offset + hd],
        ]);
        ctx.stroke();
        var o = this.toScreen(0, 0);
        ctx.fillStyle = 'rgba(200,200,200,0.5)';
        ctx.beginPath();
        ctx.arc(o[0], o[1], 3, 0, Math.PI * 2);
        ctx.fill();
    };

    P._renderDraft = function () {
        if (!this.draft || this.draft.vertices.length === 0) return;
        var ctx = this.ctx, verts = this.draft.vertices;
        ctx.strokeStyle = '#7cf';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        for (var i = 0; i < verts.length; i++) {
            var p = this.toScreen(verts[i][0], verts[i][1]);
            if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        for (var j = 0; j < verts.length; j++) {
            var q = this.toScreen(verts[j][0], verts[j][1]);
            ctx.fillStyle = (j === 0) ? '#ffd76a' : '#7cf';
            ctx.beginPath();
            ctx.arc(q[0], q[1], 6, 0, Math.PI * 2);
            ctx.fill();
        }
    };

    P._renderMarkers = function () {
        var ctx = this.ctx;
        for (var i = 0; i < this.markers.length; i++) {
            var m = this.markers[i];
            var p = this.toScreen(m.x, m.y);
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
    };

    // -- drawing a new polygon ------------------------------------------

    P.startDraft = function () {
        this.draft = {vertices: []};
        this.render();
    };

    P.cancelDraft = function () {
        this.draft = null;
        this.render();
    };

    // Closes the drawing when it has three corners; true when it did.
    P.finishDraft = function () {
        if (!this.draft || this.draft.vertices.length < 3) return false;
        var verts = this.draft.vertices.slice();
        this.draft = null;
        if (this.opts.onDraftClose) this.opts.onDraftClose(verts);
        this.render();
        return true;
    };

    P._endDrag = function () {
        if (!this.drag) return;
        var index = this.drag.index;
        this.drag = null;
        this._frozenExtent = null;
        if (this.opts.onDragEnd) this.opts.onDragEnd(index);
        this.render();
    };

    P._bind = function () {
        var plan = this, canvas = this.canvas;
        canvas.addEventListener('mousedown', function (evt) {
            if (plan.draft) return;
            var m = plan.pointer(evt);
            var vi = plan.vertexAt(m[0], m[1]);
            if (vi !== null) {
                plan._frozenExtent = plan._liveExtent();
                plan.drag = {index: vi};
                return;
            }
            if (plan.opts.onPress) plan.opts.onPress(plan.toWorld(m[0], m[1]));
            plan.render();
        });
        canvas.addEventListener('mousemove', function (evt) {
            if (!plan.drag) return;
            var verts = plan.opts.editable ? plan.opts.editable() : null;
            if (!verts || !verts[plan.drag.index]) return;
            var m = plan.pointer(evt);
            verts[plan.drag.index] = plan.toWorld(m[0], m[1]);
            plan.render();
            if (plan.opts.onDragMove) plan.opts.onDragMove(plan.drag.index);
        });
        var end = function () { plan._endDrag(); };
        // Released outside the canvas or the window lost focus mid-drag.
        canvas.addEventListener('mouseup', end);
        window.addEventListener('mouseup', end);
        window.addEventListener('blur', end);
        canvas.addEventListener('click', function (evt) {
            if (!plan.draft) return;
            var m = plan.pointer(evt);
            var verts = plan.draft.vertices;
            if (verts.length >= 3) {
                var p0 = plan.toScreen(verts[0][0], verts[0][1]);
                var dx = p0[0] - m[0], dy = p0[1] - m[1];
                if (dx * dx + dy * dy <= CLOSE_HIT_SQ) {
                    plan.finishDraft();
                    return;
                }
            }
            verts.push(plan.toWorld(m[0], m[1]));
            plan.render();
        });
        canvas.addEventListener('dblclick', function () { plan.finishDraft(); });
    };

    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.PolygonPlan = PolygonPlan;
    window.OpenFollow.pointInPolygon = pointInPolygon;
})();
