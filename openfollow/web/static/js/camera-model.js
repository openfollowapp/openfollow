// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
//
// The saved camera in the browser: the maths of scene/solver.py (project_points +
// apply_overlay_distortion, invert_overlay_distortion + unproject_to_plane) for one
// picture size, so an editor follows the pointer without a round trip per step.
// tests/test_camera_model_js.py pins it to the Python.
(function () {
    'use strict';

    // Smallest positive root of 1 + b s + a s^2, Infinity when there is none.
    function firstPositiveRoot(a, b) {
        if (a === 0) return b < 0 ? -1 / b : Infinity;
        var disc = b * b - 4 * a;
        if (disc < 0) return Infinity;
        var q = -0.5 * (b + (b < 0 ? -1 : 1) * Math.sqrt(disc));
        var best = Infinity;
        [q / a, 1 / q].forEach(function (s) { if (s > 0 && s < best) best = s; });
        return best;
    }

    // The screen radius where the lens map first stops rising: its pole or its fold (lens_model.lens_limit_radius).
    function limitRadius(k1, k2) {
        return Math.sqrt(Math.min(firstPositiveRoot(k2, k1), firstPositiveRoot(-3 * k2, -k1)));
    }

    // The screen radius whose pinhole radius is ru, by Newton kept inside [0, rLim]: a pinhole radius past a
    // fold has none, and lands on the fold as in the solver.
    function distortRadius(ru, k1, k2, rLim) {
        var lo = 0, hi = rLim, r = Math.min(ru, hi);
        for (var n = 0; n < 80; n++) {
            var s = r * r, h = ru * ((k2 * s + k1) * s + 1) - r;
            var step = h / (ru * r * (4 * k2 * s + 2 * k1) - 1);
            if (h > 0) lo = r;
            if (h < 0) hi = r;
            var cand = r - step, moved = cand >= lo && cand <= hi ? cand : 0.5 * (lo + hi);
            var done = Math.abs(moved - r) <= 1e-13;
            r = moved;
            if (done) break;
        }
        return r;
    }

    function num(v) { return typeof v === 'number' && isFinite(v) ? v : 0; }

    // cam: {pos_x, pos_y, pos_z, pitch, yaw, roll, fov (horizontal), k1, k2, cx, cy}; a w x h picture.
    function CameraModel(cam, w, h) {
        this.w = w;
        this.h = h;
        this.pos = [num(cam.pos_x), num(cam.pos_z), -num(cam.pos_y)];
        var a = num(cam.pitch) * Math.PI / 180, b = -num(cam.yaw) * Math.PI / 180, c = num(cam.roll) * Math.PI / 180;
        var ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b), cc = Math.cos(c), sc = Math.sin(c);
        this.rot = [
            [cb * cc + sb * sa * sc, -cb * sc + sb * sa * cc, sb * ca],
            [ca * sc, ca * cc, -sa],
            [-sb * cc + cb * sa * sc, sb * sc + cb * sa * cc, cb * ca],
        ];
        this.aspect = w / h;
        var vfov = 2 * Math.atan(Math.tan(num(cam.fov) * Math.PI / 360) / this.aspect);
        this.f = 1 / Math.tan(vfov / 2);
        this.k1 = num(cam.k1);
        this.k2 = num(cam.k2);
        this.cx = num(cam.cx);
        this.cy = num(cam.cy);
        this.hd = 0.5 * Math.hypot(w, h);
        this.rLim = limitRadius(this.k1, this.k2);
        this.lens = this.k1 !== 0 || this.k2 !== 0;
    }

    var P = CameraModel.prototype;

    // The picture point of world point [x, y, z], lens included; null at or behind the camera.
    P.project = function (pt) {
        var t = [pt[0] - this.pos[0], pt[2] - this.pos[1], -pt[1] - this.pos[2]], R = this.rot;
        var v = [0, 1, 2].map(function (i) { return R[0][i] * t[0] + R[1][i] * t[1] + R[2][i] * t[2]; });
        if (!(v[2] < -1e-9)) return null;
        var sx = ((this.f / this.aspect) * (v[0] / -v[2]) + 1) / 2 * this.w;
        var sy = (1 - this.f * (v[1] / -v[2])) / 2 * this.h;
        var out = this._distort(sx, sy);
        return isFinite(out[0]) && isFinite(out[1]) ? out : null;
    };

    // The stage point [x, y] under picture point [px, py] on the plane at height z; null off the stage.
    P.unproject = function (q, z) {
        var pin = this._undistort(q[0], q[1]);
        if (!pin) return null;
        var d = [((pin[0] / this.w) * 2 - 1) * this.aspect / this.f, (1 - (pin[1] / this.h) * 2) / this.f, -1], R = this.rot;
        var wd = [0, 1, 2].map(function (i) { return R[i][0] * d[0] + R[i][1] * d[1] + R[i][2] * d[2]; });
        if (Math.abs(wd[1]) < 1e-12) return null;
        var t = (z - this.pos[1]) / wd[1];
        if (!(t > 0)) return null;
        var out = [this.pos[0] + t * wd[0], -(this.pos[2] + t * wd[2])];
        return isFinite(out[0]) && isFinite(out[1]) ? out : null;
    };

    P._distort = function (sx, sy) {
        if (!this.lens && this.cx === 0 && this.cy === 0) return [sx, sy];
        var fx = this.w / 2, fy = this.h / 2, ox = fx + this.cx * this.hd, oy = fy + this.cy * this.hd;
        if (!this.lens) return [sx + (ox - fx), sy + (oy - fy)];
        var dx = sx - fx, dy = sy - fy, ru = Math.hypot(dx, dy) / this.hd;
        var scale = ru > 0 ? distortRadius(ru, this.k1, this.k2, this.rLim) / ru : 1;
        return [ox + dx * scale, oy + dy * scale];
    };

    P._undistort = function (px, py) {
        if (!this.lens && this.cx === 0 && this.cy === 0) return [px, py];
        var fx = this.w / 2, fy = this.h / 2;
        var dx = px - (fx + this.cx * this.hd), dy = py - (fy + this.cy * this.hd);
        var s = (dx * dx + dy * dy) / (this.hd * this.hd);
        if (!(s < this.rLim * this.rLim)) return null;
        var scale = 1 / ((this.k2 * s + this.k1) * s + 1);
        return [fx + dx * scale, fy + dy * scale];
    };

    // World points a..b in n chords, as the lens bows the straight segment between them.
    P.segment = function (a, b, n) {
        var out = [];
        for (var k = 0; k <= n; k++) {
            var t = k / n;
            out.push(this.project([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]));
        }
        return out;
    };

    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.CameraModel = CameraModel;
})();
