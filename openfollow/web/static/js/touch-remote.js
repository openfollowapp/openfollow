/*
 * Touch remote: drives one controlled marker from a tablet over /api/remote/*.
 *
 * While the card is open it sends the stick (X stage left, Y upstage, -1..1, already turned
 * for "Facing FOH") and the Z notches turned since the last message every DRIVE_MS; that
 * message is also the heartbeat, so the station stops the marker when it stops arriving.
 * One request is in flight at a time, so messages never overtake each other.
 */
(function () {
    'use strict';

    var DRIVE_MS = 50;
    var STATE_MS = 500;

    // Pure maths, exported for tests.
    function stickVector(dx, dy, travel, flip) {
        var len = Math.hypot(dx, dy);
        if (len > travel) { dx *= travel / len; dy *= travel / len; }
        var sx = dx / travel, sy = -dy / travel;
        return { knobX: dx, knobY: dy, sx: flip ? -sx : sx, sy: flip ? -sy : sy };
    }

    // Finger travel (px, down positive) into whole notches up, keeping the remainder.
    function drumNotches(acc, dy, notchPx) {
        acc -= dy;
        var notches = acc > 0 ? Math.floor(acc / notchPx) : -Math.floor(-acc / notchPx);
        return { notches: notches, acc: acc - notches * notchPx };
    }

    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.touchRemote = { stickVector: stickVector, drumNotches: drumNotches };

    var card = document.getElementById('remote-card');
    var launcher = document.getElementById('remote-launcher');
    if (!card || !launcher) return;

    var $ = function (id) { return document.getElementById(id); };
    var units = window.OpenFollow.units;
    var store = {
        get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
        set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* blocked storage */ } }
    };

    // ``gen`` counts openings and closings: an answer from an earlier one changes nothing.
    var st = {
        gen: 0, reopening: false, open: false, token: null, marker: null, lost: false, state: null,
        stick: { sx: 0, sy: 0 }, notches: 0, inFlight: false, flip: store.get('of.remote.flip') === '1',
        left: store.get('of.remote.hand') === 'left', xyzOpen: false, driveTimer: null, stateTimer: null
    };

    function post(path, body) {
        return fetch('/api/remote/' + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({ token: st.token }, body || {})),
            credentials: 'same-origin'
        });
    }

    function length(m) { return units ? units.formatLength(m) : m.toFixed(3) + ' m'; }

    function setOffline(off) { $('remote-offline').hidden = !off; }

    // A status the station answered with: 403 switched off, 404 session ended, 409 marker gone
    // (the state says whether another remote took it or the station let go).
    function handleStatus(res) {
        if (res.status === 403) { shutDown(); return false; }
        if (res.status === 404) { reopen(); return false; }
        if (res.status === 409) { stateTick(); return false; }
        return res.ok;
    }

    function shutDown() {
        closeCard(false);
        launcher.hidden = true;
    }

    // One reopen at a time: every request that learns of the ended session lands here.
    function reopen() {
        if (st.reopening || !st.open) return;
        var keep = st.lost ? null : st.marker;
        st.reopening = true;
        st.token = null;
        st.marker = null;
        startSession().then(function () {
            st.reopening = false;
            if (keep !== null) hold(keep);
        });
    }

    function startSession() {
        var gen = st.gen;
        st.token = null;
        return post('open').then(function (res) {
            if (gen !== st.gen || !handleStatus(res)) return;
            return res.json().then(function (data) {
                if (gen !== st.gen) return;
                st.token = data.token;
                setOffline(false);
            });
        }).catch(function () { setOffline(true); });
    }

    function hold(markerId) {
        if (!st.open || !st.token) return;
        var gen = st.gen;
        post('hold', { marker: markerId }).then(function (res) {
            if (gen !== st.gen || res.status === 409) return;
            if (!handleStatus(res)) return;
            return res.json().then(function (data) {
                if (gen !== st.gen) return;
                st.marker = markerId;
                st.lost = false;
                $('remote-detection-off').hidden = !data.detection_off;
                render();
            });
        }).catch(function () { setOffline(true); });
    }

    function driveTick() {
        if (!st.open || !st.token || st.marker === null || st.lost || st.inFlight || document.hidden) return;
        var notches = st.notches;
        st.notches = 0;
        st.inFlight = true;
        post('drive', { marker: st.marker, sx: st.stick.sx, sy: st.stick.sy, z_notches: notches })
            .then(function (res) { setOffline(false); handleStatus(res); })
            .catch(function () { st.notches += notches; setOffline(true); })
            .then(function () { st.inFlight = false; });
    }

    function stateTick() {
        if (!st.open || !st.token || document.hidden) return;
        var gen = st.gen;
        fetch('/api/remote/state?token=' + encodeURIComponent(st.token), { credentials: 'same-origin' })
            .then(function (res) {
                if (gen !== st.gen || !handleStatus(res)) return;
                return res.json().then(function (data) {
                    if (gen !== st.gen) return;
                    st.state = data;
                    if (data.lost) st.lost = true;
                    else if (data.held === null) st.marker = null;
                    render();
                });
            })
            .catch(function () { setOffline(true); });
    }

    function action(kind, extra) {
        if (st.marker === null || st.lost) return;
        post('action', Object.assign({ marker: st.marker, action: kind }, extra || {}))
            .then(handleStatus)
            .catch(function () { setOffline(true); });
    }

    function current() {
        var s = st.state;
        if (!s) return null;
        for (var i = 0; i < s.markers.length; i++) if (s.markers[i].id === st.marker) return s.markers[i];
        return null;
    }

    function renderChips() {
        var chips = $('remote-chips');
        var s = st.state;
        var ids = s ? s.markers.map(function (m) { return m.id; }).join(',') : '';
        if (chips.dataset.ids !== ids) {
            chips.dataset.ids = ids;
            chips.textContent = '';
            (s ? s.markers : []).forEach(function (m) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'remote-chip';
                b.dataset.id = m.id;
                var dot = document.createElement('span');
                dot.className = 'remote-chip-dot';
                if (m.color) dot.style.background = m.color;
                b.appendChild(dot);
                b.appendChild(document.createTextNode(m.id + (m.name ? ' · ' + m.name : '')));
                b.addEventListener('click', function () { hold(m.id); });
                chips.appendChild(b);
            });
        }
        Array.prototype.forEach.call(chips.children, function (b) {
            b.setAttribute('aria-pressed', String(Number(b.dataset.id) === st.marker && !st.lost));
        });
    }

    function render() {
        var s = st.state;
        document.body.classList.toggle('remote-left', st.left);
        renderChips();
        var m = current();
        var held = st.marker !== null && !st.lost;
        $('remote-held').hidden = !held;
        $('remote-held').textContent = held ? 'Remote has ' + st.marker + (m && m.name ? ' · ' + m.name : '') : '';
        $('remote-lost').hidden = !st.lost;
        var locked = !!(s && held && s.locked.indexOf(st.marker) >= 0);
        $('remote-on-path').hidden = !locked;
        $('remote-z').classList.toggle('off', locked);
        var pf = $('remote-pathfinder');
        pf.hidden = !(s && s.paths_on);
        pf.textContent = s && s.recording ? 'Record point' : 'Pathfinder';
        pf.classList.toggle('locked', locked);
        $('remote-flip').setAttribute('aria-pressed', String(st.flip));
        $('remote-up').textContent = st.flip ? 'DOWNSTAGE' : 'UPSTAGE';
        $('remote-down').textContent = st.flip ? 'UPSTAGE' : 'DOWNSTAGE';
        $('remote-hand').textContent = st.left ? 'Left hand' : 'Right hand';
        $('remote-xyz').setAttribute('aria-pressed', String(st.xyzOpen));
        $('remote-xyz-row').hidden = !st.xyzOpen;
        if (s) $('remote-notch').textContent = length(s.z_step_m);
        var readout = $('remote-readout');
        readout.textContent = '';
        if (m && m.pos) {
            ['X', 'Y', 'Z'].forEach(function (axis, i) {
                var span = document.createElement('span');
                span.textContent = axis + ' ' + length(m.pos[i]);
                readout.appendChild(span);
            });
        }
    }

    function fillXyz() {
        var m = current();
        if (!m || !m.pos) return;
        $('remote-x').value = length(m.pos[0]);
        $('remote-y').value = length(m.pos[1]);
        $('remote-z-in').value = length(m.pos[2]);
    }

    function parse(raw) {
        if (units) return units.parseLength(raw);
        var v = parseFloat(raw);
        return isFinite(v) ? v : NaN;
    }

    // Opening takes no marker: tapping a chip does.
    function openCard() {
        st.gen += 1;
        st.open = true;
        card.hidden = false;
        launcher.setAttribute('aria-expanded', 'true');
        render();
        startSession().then(stateTick);
        st.driveTimer = window.setInterval(driveTick, DRIVE_MS);
        st.stateTimer = window.setInterval(stateTick, STATE_MS);
    }

    function closeCard(tellStation) {
        if (tellStation !== false && st.token) post('close').catch(function () { /* the station forgets it */ });
        st.gen += 1;
        st.reopening = false;
        st.open = false;
        st.token = null;
        st.marker = null;
        st.lost = false;
        card.hidden = true;
        launcher.setAttribute('aria-expanded', 'false');
        window.clearInterval(st.driveTimer);
        window.clearInterval(st.stateTimer);
        releaseStick();
    }

    launcher.addEventListener('click', function () { if (st.open) closeCard(); else openCard(); });
    $('remote-close').addEventListener('click', function () { closeCard(); });

    // Stick
    var stick = $('remote-stick'), knob = $('remote-knob'), stickPointer = null;
    function moveStick(e) {
        var r = stick.getBoundingClientRect();
        var travel = r.width / 2 - knob.offsetWidth / 2;
        var v = stickVector(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2), travel, st.flip);
        knob.style.transform = 'translate(' + v.knobX + 'px,' + v.knobY + 'px)';
        st.stick = { sx: v.sx, sy: v.sy };
    }
    function releaseStick() {
        stickPointer = null;
        stick.classList.remove('active');
        knob.style.transform = '';
        st.stick = { sx: 0, sy: 0 };
    }
    stick.addEventListener('pointerdown', function (e) {
        stickPointer = e.pointerId;
        stick.setPointerCapture(e.pointerId);
        stick.classList.add('active');
        moveStick(e);
    });
    stick.addEventListener('pointermove', function (e) { if (e.pointerId === stickPointer) moveStick(e); });
    ['pointerup', 'pointercancel'].forEach(function (t) {
        stick.addEventListener(t, function (e) { if (e.pointerId === stickPointer) releaseStick(); });
    });

    // Z drum
    var drum = $('remote-drum'), ticks = $('remote-ticks'), drumPointer = null, drumY = 0, drumAcc = 0, drumOffset = 0;
    drum.addEventListener('pointerdown', function (e) {
        drumPointer = e.pointerId;
        drum.setPointerCapture(e.pointerId);
        drumY = e.clientY;
        drumAcc = 0;
    });
    drum.addEventListener('pointermove', function (e) {
        if (e.pointerId !== drumPointer) return;
        var dy = e.clientY - drumY;
        drumY = e.clientY;
        drumOffset = (drumOffset + dy) % 20;
        ticks.style.transform = 'translateY(' + drumOffset + 'px)';
        var notchPx = st.state ? st.state.z_notch_px : 20;
        var r = drumNotches(drumAcc, dy, notchPx);
        drumAcc = r.acc;
        if (r.notches) {
            st.notches += r.notches;
            drum.classList.add('notched');
            window.setTimeout(function () { drum.classList.remove('notched'); }, 70);
            if (navigator.vibrate) navigator.vibrate(8);
        }
    });
    ['pointerup', 'pointercancel'].forEach(function (t) {
        drum.addEventListener(t, function (e) { if (e.pointerId === drumPointer) drumPointer = null; });
    });

    // Buttons
    $('remote-reset').addEventListener('click', function () { action('reset'); });
    $('remote-pathfinder').addEventListener('click', function () { action('pathfinder'); });
    $('remote-xyz').addEventListener('click', function () {
        st.xyzOpen = !st.xyzOpen;
        if (st.xyzOpen) fillXyz();
        render();
    });
    $('remote-go').addEventListener('click', function () {
        var x = parse($('remote-x').value), y = parse($('remote-y').value), z = parse($('remote-z-in').value);
        if (!isFinite(x) || !isFinite(y) || !isFinite(z)) { fillXyz(); return; }
        action('glide', { x: x, y: y, z: z });
        if (document.activeElement) document.activeElement.blur();
    });
    $('remote-flip').addEventListener('click', function () {
        st.flip = !st.flip;
        store.set('of.remote.flip', st.flip ? '1' : '0');
        render();
    });
    $('remote-hand').addEventListener('click', function () {
        st.left = !st.left;
        store.set('of.remote.hand', st.left ? 'left' : 'right');
        render();
    });

    // A hidden page sends nothing: the station's dead-man stops the marker.
    document.addEventListener('visibilitychange', function () { if (document.hidden) releaseStick(); });
    document.body.classList.toggle('remote-left', st.left);
})();
