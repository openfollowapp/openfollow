/*
 * OpenFollow failed-save feedback.
 *
 * The red counterpart of the green save flash: a save that fails rings its
 * form red once and leaves a line under the form's actions saying what the
 * station observed and one next step. The line stays until the next edit in
 * that form or the next save from it.
 *
 * HTMX saves (any non-GET request) are handled here on ``document``. A
 * script-driven save calls ``window.OpenFollow.saveError``:
 *
 *   show(box, info, lead, near) ring ``box``; write ``info`` after ``near``
 *                              (default: the box's last actions row)
 *   clear(box)                 remove the line
 *   origin()                   the section last clicked outside a dialog
 *   fromResponse(res)          -> Promise<info> for a failed fetch Response
 *   fromText(status, text)     -> info for a body already read
 *   UNREACHABLE                info for a request that got no answer
 *
 * ``info`` is ``{error, action, href}``, the shape the station answers a
 * refused change with; ``lead`` defaults to "Not saved.". A ``box`` that is
 * hidden by then (a dialog that closed before its request failed) hands the
 * line to ``origin()``, where the operator started.
 */
(function () {
    'use strict';

    var UNREACHABLE = {
        error: 'The station did not answer.',
        action: 'Check that it is switched on and on this network, then save again.',
        href: '',
    };
    var BOX_SELECTOR = 'form.section, .save-flash, .section, .modal-card, [role="dialog"]';
    var ANCHOR_SELECTOR = '.actions, .wizard-nav, .modal-footer';
    var MAX_TEXT = 200;
    var lastOrigin = null;

    function sentence(text) {
        var t = String(text || '').trim();
        return t && !/[.!?]$/.test(t) ? t + '.' : t;
    }

    function fromText(status, text) {
        // Many routes answer JSON without saying so, so any body may be JSON.
        try {
            var data = JSON.parse(text);
            if (data && data.error) {
                return { error: sentence(data.error), action: data.action || '', href: data.href || '' };
            }
        } catch (e) { /* not JSON: read it as text */ }
        // Parsed inert: DOMParser never runs scripts or loads resources.
        var doc = new DOMParser().parseFromString(String(text || ''), 'text/html');
        var pre = doc.querySelector('pre');
        var said = ((pre || doc.body || {}).textContent || '').trim();
        if (said && said.length <= MAX_TEXT) {
            return { error: sentence(said), action: '', href: '' };
        }
        return { error: 'The station answered with an error (HTTP ' + status + ').', action: '', href: '' };
    }

    function fromResponse(res) {
        return res.text().then(
            function (text) { return fromText(res.status, text); },
            function () { return fromText(res.status, ''); }
        );
    }

    function clear(box) {
        if (!box || !box._saveError) return;
        box._saveError.remove();
        box._saveError = null;
    }

    function show(box, info, lead, near) {
        if (box && box.closest('[hidden]')) box = lastOrigin;
        if (!box) return;
        var line = box._saveError;
        if (!line || !box.contains(line)) {
            line = document.createElement('p');
            line.className = 'save-error';
            line.setAttribute('role', 'alert');
            box._saveError = line;
            box.addEventListener('input', function () { clear(box); }, { once: true });
        }
        var anchors = box.querySelectorAll(ANCHOR_SELECTOR);
        if (!near || !box.contains(near)) near = anchors.length ? anchors[anchors.length - 1] : null;
        if (near) near.after(line);
        else box.appendChild(line);
        var words = [lead === undefined ? 'Not saved.' : lead, info.error].filter(Boolean).join(' ');
        line.replaceChildren(words);
        if (info.action) {
            line.append(' ');
            if (info.href) {
                var link = document.createElement('a');
                link.href = info.href;
                link.textContent = info.action;
                line.append(link);
            } else {
                line.append(info.action);
            }
        }
        // Restart the flash even when the previous one is still running.
        clearTimeout(box._saveErrorTimer);
        box.classList.remove('save-failed');
        void box.offsetWidth;
        box.classList.add('save-failed');
        box._saveErrorTimer = setTimeout(function () { box.classList.remove('save-failed'); }, 600);
    }

    function boxFor(elt) {
        if (!elt || !elt.closest) return null;
        return elt.closest(BOX_SELECTOR) || elt.closest('form');
    }

    function isSave(evt) {
        var cfg = evt.detail && evt.detail.requestConfig;
        return !!(cfg && cfg.verb && String(cfg.verb).toLowerCase() !== 'get');
    }

    // Capture phase, so a handler that stops the click cannot hide it.
    document.addEventListener('click', function (evt) {
        var target = evt.target;
        if (!target || !target.closest || target.closest('#modal-root')) return;
        lastOrigin = boxFor(target) || lastOrigin;
    }, true);
    document.addEventListener('htmx:beforeRequest', function (evt) {
        if (isSave(evt)) clear(boxFor(evt.detail.elt));
    });
    document.addEventListener('htmx:responseError', function (evt) {
        if (!isSave(evt)) return;
        var xhr = evt.detail.xhr;
        show(boxFor(evt.detail.elt), fromText(xhr.status, xhr.responseText));
    });
    document.addEventListener('htmx:sendError', function (evt) {
        if (isSave(evt)) show(boxFor(evt.detail.elt), UNREACHABLE);
    });

    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.saveError = {
        show: show,
        clear: clear,
        boxFor: boxFor,
        origin: function () { return lastOrigin; },
        fromResponse: fromResponse,
        fromText: fromText,
        UNREACHABLE: UNREACHABLE,
    };
})();
