// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
// Copyright (C) 2026 Michel Honold and Paul Hermann
//
// A top-down stage plan on a <canvas> for drawing and editing polygons in
// PSN-absolute world metres (X stage left, Y upstage): the main grid, the live
// markers, and drag handles on one editable polygon, whose edges add a corner.
// A polygon the page names under the pointer (its label) moves whole.
// The page owns its polygons and draws them in ``opts.drawPolygons``.
(function () {
    'use strict';

    var PAD = 20;
    var HANDLE_HIT_SQ = 49;  // 7 px
    var EDGE_HIT_SQ = 100;  // 10 px from an edge of the editable polygon

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

    // Whether the solid zone from bottom to top over its corners hides a stage point from the camera at eye.
    function hiddenByZone(pt, eye, v, bottom, top) {
        var dx = pt[0] - eye[0], dy = pt[1] - eye[1], dz = pt[2] - eye[2], t0, t1;
        if (Math.abs(dz) < 1e-12) {
            if (eye[2] < bottom || eye[2] > top) return false;
            t0 = 0; t1 = 1;
        } else {
            var ta = (bottom - eye[2]) / dz, tb = (top - eye[2]) / dz;
            t0 = Math.max(0, Math.min(ta, tb)); t1 = Math.min(1, Math.max(ta, tb));
            if (t1 - t0 < 1e-12) return false;
        }
        // The sight line within the zone's height, in plan: hidden when any stretch of it is inside the corners.
        var ax = eye[0] + t0 * dx, ay = eye[1] + t0 * dy, sx = (t1 - t0) * dx, sy = (t1 - t0) * dy, cuts = [0, 1];
        v.forEach(function (c, i) {
            var f = v[(i + 1) % v.length], ux = f[0] - c[0], uy = f[1] - c[1], den = sx * uy - sy * ux;
            if (Math.abs(den) < 1e-12) return;
            var s = ((c[0] - ax) * uy - (c[1] - ay) * ux) / den, u = ((c[0] - ax) * sy - (c[1] - ay) * sx) / den;
            if (s > 0 && s < 1 && u >= 0 && u <= 1) cuts.push(s);
        });
        cuts.sort(function (a, b) { return a - b; });
        for (var k = 0; k + 1 < cuts.length; k++) {
            var m = (cuts[k] + cuts[k + 1]) / 2;
            if (cuts[k + 1] - cuts[k] > 1e-9 && pointInPolygon(ax + sx * m, ay + sy * m, v)) return true;
        }
        return false;
    }

    // opts:
    //   polygons()          every committed polygon, for fitting the view
    //   editable()          the vertex array that gets drag handles, or null
    //   drawPolygons(plan)  draws the page's polygons between grid and markers
    //   onPress(world)      a press that hit no handle (selection)
    //   onDragMove(index)   a handle moved; the array is already updated
    //   onDragEnd(index)    a drag ended
    //   onInsert(index)     a press on an edge added a corner at index, now held
    //   labelAt(sx, sy)     the polygon whose label is at a canvas point, or null
    //   grab(polygon)       its vertex array, which a press on its label moves whole
    //   onGrabMove()        a held polygon moved; its array is already updated
    //   onGrabEnd(moved)    a held polygon was let go
    //   onRender()          after every render (buttons, status)
    function PolygonPlan(canvas, opts) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.opts = opts || {};
        this.grid = {width: 10, depth: 10, spacing: 1, x_offset: 0, y_offset: 0};
        this.markers = [];
        this.drag = null;  // {index} while a handle is held, {whole, from, before} while a polygon is
        this.hover = null;  // the point on an edge a press would add a corner at
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

    // The editable polygon's edge nearest a canvas point within reach: the corner it starts at and the point on it.
    P.edgeAt = function (sx, sy) {
        var verts = this.opts.editable ? this.opts.editable() : null, best = null;
        if (!verts || verts.length < 2) return null;
        for (var i = 0; i < verts.length; i++) {
            var a = this.toScreen(verts[i][0], verts[i][1]);
            var b = this.toScreen(verts[(i + 1) % verts.length][0], verts[(i + 1) % verts.length][1]);
            var ux = b[0] - a[0], uy = b[1] - a[1], len = ux * ux + uy * uy;
            var t = len ? Math.max(0, Math.min(1, ((sx - a[0]) * ux + (sy - a[1]) * uy) / len)) : 0;
            var qx = a[0] + t * ux, qy = a[1] + t * uy, d = (qx - sx) * (qx - sx) + (qy - sy) * (qy - sy);
            if (d <= EDGE_HIT_SQ && (!best || d < best.d)) best = {index: i, point: [qx, qy], d: d};
        }
        return best;
    };

    // -- drawing --------------------------------------------------------

    P.render = function () {
        this._renderGrid();
        if (this.opts.drawPolygons) this.opts.drawPolygons(this);
        this._renderHover();
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

    // The corner a press would add, as the camera view marks it: a dashed accent ring.
    P._renderHover = function () {
        if (!this.hover) return;
        var ctx = this.ctx;
        ctx.strokeStyle = '#ffbc00';
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.arc(this.hover[0], this.hover[1], 7, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
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

    P._endDrag = function () {
        var drag = this.drag;
        if (!drag) return;
        this.drag = null;
        this._frozenExtent = null;
        if (drag.whole) {
            if (this.opts.onGrabEnd) this.opts.onGrabEnd(!!drag.moved);
        } else if (this.opts.onDragEnd) this.opts.onDragEnd(drag.index);
        this.render();
    };

    P._labelAt = function (sx, sy) {
        return this.opts.labelAt ? this.opts.labelAt(sx, sy) : null;
    };

    // The pointer over a label moves its polygon; over an edge of the editable one it adds a corner.
    P._cursor = function (sx, sy) {
        if (!this.canvas.style) return;
        var over = this.vertexAt(sx, sy) !== null ? '' : this._labelAt(sx, sy) !== null ? 'move' : this.hover ? 'copy' : '';
        if (this.canvas.style.cursor !== over) this.canvas.style.cursor = over;
    };

    P._bind = function () {
        var plan = this, canvas = this.canvas;
        canvas.addEventListener('mousedown', function (evt) {
            var m = plan.pointer(evt);
            var vi = plan.vertexAt(m[0], m[1]);
            if (vi !== null) {
                plan._frozenExtent = plan._liveExtent();
                plan.drag = {index: vi};
                return;
            }
            var label = plan._labelAt(m[0], m[1]);
            var whole = label !== null && plan.opts.grab ? plan.opts.grab(label) : null;
            if (whole) {
                plan._frozenExtent = plan._liveExtent();
                plan.hover = null;
                plan.drag = {whole: whole, from: plan.toWorld(m[0], m[1]), before: whole.map(function (v) { return v.slice(); })};
                plan.render();
                return;
            }
            var edge = plan.edgeAt(m[0], m[1]);
            if (edge) {
                plan._frozenExtent = plan._liveExtent();
                plan.opts.editable().splice(edge.index + 1, 0, plan.toWorld(edge.point[0], edge.point[1]));
                plan.drag = {index: edge.index + 1};
                plan.hover = null;
                if (plan.opts.onInsert) plan.opts.onInsert(edge.index + 1);
                plan.render();
                return;
            }
            if (plan.opts.onPress) plan.opts.onPress(plan.toWorld(m[0], m[1]));
            plan.render();
        });
        canvas.addEventListener('mousemove', function (evt) {
            if (!plan.drag) {
                var p = plan.pointer(evt);
                var edge = plan.vertexAt(p[0], p[1]) === null && plan._labelAt(p[0], p[1]) === null
                    ? plan.edgeAt(p[0], p[1]) : null;
                var hover = edge ? edge.point : null;
                if (JSON.stringify(hover) !== JSON.stringify(plan.hover)) {
                    plan.hover = hover;
                    plan.render();
                }
                plan._cursor(p[0], p[1]);
                return;
            }
            if (plan.drag.whole) {
                var w = plan.toWorld.apply(plan, plan.pointer(evt)), drag = plan.drag;
                var dx = w[0] - drag.from[0], dy = w[1] - drag.from[1];
                drag.before.forEach(function (v, i) { drag.whole[i] = [v[0] + dx, v[1] + dy]; });
                drag.moved = true;
                plan.render();
                if (plan.opts.onGrabMove) plan.opts.onGrabMove();
                return;
            }
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
        canvas.addEventListener('mouseleave', function () {
            if (!plan.hover) return;
            plan.hover = null;
            plan.render();
        });
    };

    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.PolygonPlan = PolygonPlan;
    window.OpenFollow.pointInPolygon = pointInPolygon;
    window.OpenFollow.hiddenByZone = hiddenByZone;
})();
