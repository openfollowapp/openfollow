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
    // A drive that hangs longer is given up, so the next heartbeat can go.
    var DRIVE_TIMEOUT_MS = 1000;

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
        gen: 0, holds: 0, opening: false, checking: false, open: false, token: null, marker: null, lost: false, state: null,
        stick: { sx: 0, sy: 0 }, notches: 0, inFlight: false, flip: store.get('of.remote.flip') === '1',
        left: store.get('of.remote.hand') === 'left', xyzOpen: false, driveTimer: null, stateTimer: null
    };

    function post(path, body, extra) {
        return fetch('/api/remote/' + path, Object.assign({
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({ token: st.token }, body || {})),
            credentials: 'same-origin'
        }, extra || {}));
    }

    function length(m) { return units ? units.formatLength(m) : m.toFixed(3) + ' m'; }

    function setOffline(off) { $('remote-offline').hidden = !off; }

    // A status the station answered with: 403 switched off, 404 session ended, 409 marker gone
    // (the state says whether another remote took it or the station let go).
    function handleStatus(res) {
        // Signed out (PIN cookie gone): reloading lands on the login page.
        if (res.status === 401) { window.location.reload(); return false; }
        if (res.status === 403) { shutDown(); return false; }
        if (res.status === 404) { reopen(); return false; }
        if (res.status === 409) {
            if (!st.checking) { st.checking = true; stateTick(); }
            return false;
        }
        // Any other failure is never silent.
        if (!res.ok) setOffline(true);
        return res.ok;
    }

    function shutDown() {
        closeCard(false);
        launcher.hidden = true;
    }

    // The station forgot the session (asleep past its timeout, or restarted): open a new one. The
    // marker may be someone else's by now, so taking it again is a chip tap, never automatic.
    function reopen() {
        if (!st.open) return;
        st.token = null;
        st.marker = null;
        st.lost = false;
        st.checking = false;
        render();
        startSession();
    }

    // One open at a time: every request that learns of an ended session, and every retry, lands here.
    function startSession() {
        if (st.opening) return Promise.resolve();
        var gen = st.gen;
        st.opening = true;
        st.token = null;
        return post('open').then(function (res) {
            if (gen !== st.gen || !handleStatus(res)) return;
            return res.json().then(function (data) {
                if (gen !== st.gen) return;
                st.token = data.token;
                setOffline(false);
            });
        }).catch(function () { setOffline(true); }).then(function () {
            if (gen === st.gen) st.opening = false;
        });
    }

    function hold(markerId) {
        if (!st.open || !st.token) return;
        var gen = st.gen;
        st.holds += 1;
        post('hold', { marker: markerId }).then(function (res) {
            if (gen !== st.gen) return;
            // Refused (the station no longer controls it): the next state redraws the chips.
            if (res.status === 409) { stateTick(); return; }
            if (!handleStatus(res)) return;
            return res.json().then(function (data) {
                if (gen !== st.gen) return;
                st.marker = markerId;
                st.lost = false;
                st.notches = 0;
                $('remote-detection-off').hidden = !data.detection_off;
                render();
            });
        }).catch(function () { setOffline(true); });
    }

    function driveTick() {
        if (!st.open || !st.token || st.marker === null || st.lost || st.checking || st.inFlight || document.hidden) return;
        var notches = st.notches, gen = st.gen;
        st.notches = 0;
        st.inFlight = true;
        var abort = window.AbortController ? new window.AbortController() : null;
        var timer = abort ? window.setTimeout(function () { abort.abort(); }, DRIVE_TIMEOUT_MS) : null;
        post('drive', { marker: st.marker, sx: st.stick.sx, sy: st.stick.sy, z_notches: notches },
            abort ? { signal: abort.signal } : null)
            .then(function (res) { if (gen === st.gen) { setOffline(false); handleStatus(res); } })
            // Notches in a drive that failed are dropped: the station may have applied them.
            .catch(function () { if (gen === st.gen) setOffline(true); })
            .then(function () {
                if (timer !== null) window.clearTimeout(timer);
                if (gen === st.gen) st.inFlight = false;
            });
    }

    function stateTick() {
        if (!st.open || document.hidden) return;
        // An open that failed (Wi-Fi gone for a moment) is retried here.
        if (!st.token) { if (!st.opening) startSession(); return; }
        var gen = st.gen, holds = st.holds;
        fetch('/api/remote/state?token=' + encodeURIComponent(st.token), { credentials: 'same-origin' })
            .then(function (res) {
                if (gen !== st.gen) return;
                st.checking = false;
                if (!handleStatus(res)) return;
                return res.json().then(function (data) {
                    if (gen !== st.gen) return;
                    st.state = data;
                    // The station's word on what this session holds wins over the card's, unless
                    // a chip was tapped after this poll went out.
                    if (holds === st.holds) {
                        st.lost = !!data.lost;
                        if (!st.lost) st.marker = data.held;
                    }
                    render();
                });
            })
            .catch(function () {
                if (gen !== st.gen) return;
                st.checking = false;
                setOffline(true);
            });
    }

    function action(kind, extra) {
        if (st.marker === null || st.lost) return;
        var gen = st.gen;
        post('action', Object.assign({ marker: st.marker, action: kind }, extra || {}))
            .then(function (res) { if (gen === st.gen) handleStatus(res); })
            .catch(function () { if (gen === st.gen) setOffline(true); });
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
        if (!held) $('remote-detection-off').hidden = true;
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
        // Up on the stick goes upstage unless the card or the station turns it around.
        var turned = st.flip !== !!(s && s.inverted);
        $('remote-up').textContent = turned ? 'DOWNSTAGE' : 'UPSTAGE';
        $('remote-down').textContent = turned ? 'UPSTAGE' : 'DOWNSTAGE';
        $('remote-hand').textContent = st.left ? 'Left hand' : 'Right hand';
        $('remote-xyz').setAttribute('aria-pressed', String(st.xyzOpen));
        $('remote-xyz-row').hidden = !st.xyzOpen;
        if (s) {
            $('remote-notch').textContent = length(s.z_step_m);
            // One tick line per notch of finger travel.
            $('remote-ticks').style.backgroundSize = '100% ' + s.z_notch_px + 'px';
        }
        if (st.xyzOpen) fillXyz();
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

    // Each field follows the marker until the operator types in it; Glide hands it back.
    var XYZ = ['remote-x', 'remote-y', 'remote-z-in'];
    var typed = {};
    XYZ.forEach(function (id) { $(id).addEventListener('input', function () { typed[id] = true; }); });

    function fillXyz() {
        var m = current();
        if (!m || !m.pos) return;
        XYZ.forEach(function (id, i) { if (!typed[id]) $(id).value = length(m.pos[i]); });
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
        // ``keepalive`` lets the close outlive a page that is going away.
        if (tellStation !== false && st.token) {
            post('close', null, { keepalive: true }).catch(function () { /* the station forgets it */ });
        }
        st.gen += 1;
        st.notches = 0;
        st.opening = false;
        st.checking = false;
        st.inFlight = false;
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
        var notchPx = st.state ? st.state.z_notch_px : 20;
        drumOffset = (drumOffset + dy) % notchPx;
        ticks.style.transform = 'translateY(' + drumOffset + 'px)';
        var r = drumNotches(drumAcc, dy, notchPx);
        drumAcc = r.acc;
        if (r.notches) {
            // Turned with no marker held, a notch goes nowhere: never saved up for a later hold.
            if (st.marker !== null && !st.lost) st.notches += r.notches;
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
        typed = {};
        render();
    });
    $('remote-go').addEventListener('click', function () {
        var x = parse($('remote-x').value), y = parse($('remote-y').value), z = parse($('remote-z-in').value);
        typed = {};
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

    // A hidden page sends nothing: the station's dead-man stops the marker. A page going away
    // gives the marker back at once.
    document.addEventListener('visibilitychange', function () { if (document.hidden) releaseStick(); });
    window.addEventListener('pagehide', function () { if (st.open) closeCard(); });
    document.body.classList.toggle('remote-left', st.left);
})();
