// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 OpenFollow Project
//
// A small DOM for running a template's script in node: markup, selectors, events,
// focus, timers and fetch, each moved by the test. Only what the scripts under test use.

var HTML_NS = 'http://www.w3.org/1999/xhtml';
var VOID = { img: 1, input: 1, br: 1, hr: 1, meta: 1, link: 1 };
var tasks = [];        // events the browser dispatches as a later task (details' toggle)
var timeouts = [];     // setTimeout callbacks; never run on their own
var intervals = [];    // setInterval callbacks, run by tickIntervals()

function kebab(k) { return k.replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); }); }

function Text(s) { this.nodeType = 3; this.textContent = String(s); this.parentNode = null; }
Text.prototype.remove = function () { if (this.parentNode) this.parentNode.removeChild(this); };

function El(tag, ns) {
    this.namespaceURI = ns || HTML_NS;
    this.tagName = this.namespaceURI === HTML_NS ? tag.toUpperCase() : tag;
    this.attrs = {};
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = {};
    this.style = {};
    this.checked = false;
    this.disabled = false;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this._value = null;
    this._open = false;
    var el = this;
    this.dataset = new Proxy({}, {
        get: function (_, k) { return typeof k === 'string' && el.hasAttribute('data-' + kebab(k)) ? el.getAttribute('data-' + kebab(k)) : undefined; },
        set: function (_, k, v) { el.setAttribute('data-' + kebab(k), String(v)); return true; },
        has: function (_, k) { return el.hasAttribute('data-' + kebab(k)); },
    });
    this.classList = {
        contains: function (c) { return el.className.split(/\s+/).indexOf(c) >= 0; },
        add: function (c) { if (!this.contains(c)) el.className = (el.className + ' ' + c).trim(); },
        remove: function (c) { el.className = el.className.split(/\s+/).filter(function (x) { return x && x !== c; }).join(' '); },
        toggle: function (c, on) { if (on === undefined) on = !this.contains(c); if (on) this.add(c); else this.remove(c); return on; },
    };
}
El.prototype.nodeType = 1;

function attrProp(name) {
    return {
        get: function () { return this.hasAttribute(name) ? this.getAttribute(name) : ''; },
        set: function (v) { this.setAttribute(name, v); },
    };
}
Object.defineProperties(El.prototype, {
    id: attrProp('id'),
    className: attrProp('class'),
    type: { get: function () { return this.getAttribute('type') || (this.tagName === 'INPUT' ? 'text' : ''); } },
    defaultValue: attrProp('value'),
    href: attrProp('href'),
    value: {
        get: function () {
            if (this.tagName === 'SELECT' && this._value === null) {
                var first = this.children[0];
                return first ? first.value : '';
            }
            if (this.tagName === 'OPTION') return this.hasAttribute('value') ? this.getAttribute('value') : this.textContent;
            if (this._value === null && (this.type === 'checkbox' || this.type === 'radio')) return this.getAttribute('value') || 'on';
            return this._value !== null ? this._value : this.getAttribute('value') || '';
        },
        set: function (v) { this._value = String(v); },
    },
    hidden: {
        get: function () { return this.hasAttribute('hidden'); },
        set: function (v) { if (v) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); },
    },
    open: {
        get: function () { return this._open; },
        set: function (v) {
            v = !!v;
            if (v === this._open) return;
            this._open = v;
            var el = this;
            tasks.push(function () { el.dispatchEvent(makeEvent('toggle', { bubbles: false })); });
        },
    },
    textContent: {
        get: function () { return this.childNodes.map(function (n) { return n.textContent; }).join(''); },
        set: function (v) { this.replaceChildren(); if (String(v)) this.appendChild(new Text(v)); },
    },
    innerHTML: {
        set: function (v) { this.replaceChildren(); parseHTML(String(v), this); },
    },
    children: { get: function () { return this.childNodes.filter(function (n) { return n.nodeType === 1; }); } },
    firstChild: { get: function () { return this.childNodes[0] || null; } },
    parentElement: { get: function () { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; } },
    nextSibling: {
        get: function () {
            var siblings = this.parentNode ? this.parentNode.childNodes : [];
            var i = siblings.indexOf(this);
            return i >= 0 ? siblings[i + 1] || null : null;
        },
    },
    previousSibling: {
        get: function () {
            var siblings = this.parentNode ? this.parentNode.childNodes : [];
            return siblings[siblings.indexOf(this) - 1] || null;
        },
    },
    isConnected: {
        get: function () {
            for (var n = this; n; n = n.parentNode) if (n === document) return true;
            return false;
        },
    },
    offsetParent: { get: function () { return this.getClientRects().length ? this.parentNode : null; } },
    offsetWidth: { get: function () { return 0; } },
});

El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
El.prototype.hasAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); };
El.prototype.removeAttribute = function (k) { delete this.attrs[k]; };

El.prototype.appendChild = function (n) {
    if (n.parentNode) n.parentNode.removeChild(n);
    this.childNodes.push(n);
    n.parentNode = this;
    return n;
};
El.prototype.append = function () {
    for (var i = 0; i < arguments.length; i++) {
        var n = arguments[i];
        this.appendChild(typeof n === 'string' ? new Text(n) : n);
    }
};
// Chrome takes focus off a field it removes while the field is still in place, firing its change and blur.
function blurIfRemoved(n) {
    var focused = document._focus;
    if (focused && n.nodeType === 1 && n.contains(focused)) {
        document._focus = null;
        leave(focused);
    }
}
El.prototype.removeChild = function (n) {
    blurIfRemoved(n);
    var i = this.childNodes.indexOf(n);
    if (i >= 0) this.childNodes.splice(i, 1);
    n.parentNode = null;
    return n;
};
El.prototype.remove = function () { if (this.parentNode) this.parentNode.removeChild(this); };
El.prototype.replaceChildren = function () {
    this.childNodes.forEach(blurIfRemoved);
    this.childNodes.slice().forEach(function (n) { n.parentNode = null; });
    this.childNodes = [];
    this.append.apply(this, arguments);
};
El.prototype.insertBefore = function (n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    var i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
    this.childNodes.splice(i, 0, n);
    n.parentNode = this;
    return n;
};
El.prototype.after = function (n) {
    var parent = this.parentNode;
    if (n.parentNode) n.parentNode.removeChild(n);
    parent.childNodes.splice(parent.childNodes.indexOf(this) + 1, 0, n);
    n.parentNode = parent;
};
El.prototype.contains = function (n) {
    for (; n; n = n.parentNode) if (n === this) return true;
    return false;
};

// -- selectors: compounds of tag, #id, .class, :checked and [attr], [attr=v], [attr^=v], [attr$=v], [attr*=v],
// joined by descendant spaces, in comma-separated groups.
function parseCompound(text) {
    var parts = [], m, rest = text;
    if ((m = /^([a-zA-Z][\w-]*|\*)/.exec(rest))) { parts.push({ tag: m[1].toLowerCase() }); rest = rest.slice(m[0].length); }
    while (rest) {
        if ((m = /^#([\w-]+)/.exec(rest))) parts.push({ attr: 'id', op: '=', value: m[1] });
        else if ((m = /^\.([\w-]+)/.exec(rest))) parts.push({ cls: m[1] });
        else if ((m = /^:checked/.exec(rest))) parts.push({ checked: true });
        else if ((m = /^\[([\w-]+)(?:([\^$*]?=)(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/.exec(rest))) {
            parts.push({ attr: m[1], op: m[2] || '', value: m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : m[5] });
        } else throw new Error('the fake DOM cannot read the selector ' + text);
        rest = rest.slice(m[0].length);
    }
    return parts;
}
function parseSelector(text) {
    return text.split(',').map(function (group) { return group.trim().split(/\s+/).map(parseCompound); });
}
function matchesCompound(el, parts) {
    if (el.nodeType !== 1) return false;
    return parts.every(function (p) {
        if (p.tag) return p.tag === '*' || el.tagName.toLowerCase() === p.tag;
        if (p.cls) return el.classList.contains(p.cls);
        if (p.checked) return !!el.checked;
        var v = el.getAttribute(p.attr);
        if (v === null) return false;
        if (p.op === '=') return v === p.value;
        if (p.op === '^=') return v.indexOf(p.value) === 0;
        if (p.op === '$=') return v.slice(-p.value.length) === p.value;
        if (p.op === '*=') return v.indexOf(p.value) >= 0;
        return true;
    });
}
function matchesChain(el, chain, scope) {
    if (!matchesCompound(el, chain[chain.length - 1])) return false;
    var rest = chain.slice(0, -1);
    for (var n = el.parentNode; rest.length && n && n !== scope; n = n.parentNode) {
        if (matchesChain(n, rest, scope)) return true;
    }
    return rest.length === 0;
}
El.prototype.matches = function (sel) {
    var el = this;
    return parseSelector(sel).some(function (chain) { return matchesChain(el, chain, null); });
};
El.prototype.closest = function (sel) {
    for (var n = this; n && n.nodeType === 1; n = n.parentNode) if (n.matches(sel)) return n;
    return null;
};
El.prototype.querySelectorAll = function (sel) {
    var chains = parseSelector(sel), out = [], scope = this;
    (function walk(node) {
        node.children.forEach(function (c) {
            if (chains.some(function (chain) { return matchesChain(c, chain, scope); })) out.push(c);
            walk(c);
        });
    })(this);
    return out;
};
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };

// -- events ----------------------------------------------------------------
function makeEvent(type, init) {
    init = init || {};
    var ev = {
        type: type,
        bubbles: init.bubbles !== false,
        defaultPrevented: false,
        stopped: false,
        preventDefault: function () { this.defaultPrevented = true; },
        stopPropagation: function () { this.stopped = true; },
    };
    Object.keys(init).forEach(function (k) { if (k !== 'bubbles') ev[k] = init[k]; });
    return ev;
}
El.prototype.addEventListener = function (type, fn, opts) {
    var capture = opts === true || !!(opts && opts.capture);
    (this.listeners[type] = this.listeners[type] || []).push({ fn: fn, capture: capture, once: !!(opts && opts.once) });
};
El.prototype.removeEventListener = function (type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter(function (l) { return l.fn !== fn; });
};
function runListeners(node, ev, capture) {
    (node.listeners[ev.type] || []).slice().forEach(function (l) {
        if (l.capture !== capture || ev.stopped) return;
        if (l.once) node.removeEventListener(ev.type, l.fn);
        ev.currentTarget = node;
        l.fn.call(node, ev);
    });
}
El.prototype.dispatchEvent = function (ev) {
    if (!('stopped' in ev)) ev = makeEvent(ev.type, { bubbles: ev.bubbles, detail: ev.detail });  // a CustomEvent
    ev.target = this;
    var path = [];
    for (var n = this; n; n = n.parentNode) path.push(n);
    for (var i = path.length - 1; i > 0 && !ev.stopped; i--) runListeners(path[i], ev, true);
    if (!ev.stopped) { runListeners(this, ev, true); runListeners(this, ev, false); }
    for (var j = 1; ev.bubbles && j < path.length && !ev.stopped; j++) runListeners(path[j], ev, false);
    return !ev.defaultPrevented;
};
// What a mouse click does: focus moves to it, a checkbox flips, a summary opens or closes its details.
var FOCUSABLE = { INPUT: 1, BUTTON: 1, SELECT: 1, TEXTAREA: 1, SUMMARY: 1, A: 1 };
El.prototype.click = function () {
    if (this.disabled) return;
    if (FOCUSABLE[this.tagName]) this.focus();
    var box = this.tagName === 'INPUT' && this.type === 'checkbox';
    if (box) this.checked = !this.checked;
    var ok = this.dispatchEvent(makeEvent('click'));
    if (box) {
        if (!ok) { this.checked = !this.checked; return; }
        this.dispatchEvent(makeEvent('input'));
        this.dispatchEvent(makeEvent('change'));
    } else if (ok && this.tagName === 'SUMMARY' && this.parentNode && this.parentNode.tagName === 'DETAILS') {
        this.parentNode.open = !this.parentNode.open;
    }
};
// Leaving a field: its change first when it was typed into (a value set by script fires none), then blur.
function leave(el) {
    if (el._typed) { el._typed = false; el.dispatchEvent(makeEvent('change')); }
    el.dispatchEvent(makeEvent('blur', { bubbles: false }));
}
El.prototype.focus = function () {
    var was = document._focus;
    if (was === this) return;
    document._focus = this;
    if (was && was.isConnected) leave(was);
    this.dispatchEvent(makeEvent('focus', { bubbles: false }));
};
El.prototype.blur = function () {
    if (document._focus !== this) return;
    document._focus = null;
    leave(this);
};
// The operator typing: focus the field and replace its text. Enter commits it as leaving would.
function typeInto(el, text) { el.focus(); el.value = text; el._typed = true; el.dispatchEvent(makeEvent('input')); }
// Tab into a field, which selects its text, then keys typed over the selection.
function tabTo(el) { el.focus(); el.select(); }
function typeKeys(el, text) {
    el.value = el.value.slice(0, el.selectionStart) + text + el.value.slice(el.selectionEnd);
    el.selectionStart = el.selectionEnd = el.selectionStart + text.length;
    el._typed = true;
    el.dispatchEvent(makeEvent('input'));
}
function enter(el) { if (el._typed) { el._typed = false; el.dispatchEvent(makeEvent('change')); } }
El.prototype.select = function () { this.selectionStart = 0; this.selectionEnd = this.value.length; };
El.prototype.setSelectionRange = function (a, b) {
    if (this.type !== 'text') throw new Error('InvalidStateError: no selection on a ' + this.type + ' input');
    this.selectionStart = a;
    this.selectionEnd = b;
};

// Rendered: in the document, and neither it nor an ancestor hidden or display: none.
El.prototype.getClientRects = function () {
    if (!this.isConnected) return [];
    for (var n = this; n && n !== document; n = n.parentNode) {
        if (n.hidden || n.style.display === 'none') return [];
    }
    return [{}];
};
// Every box is 20 px tall at the top of the page: a drop at clientY 0-9 is in a row's upper half.
El.prototype.getBoundingClientRect = function () { return { x: 0, y: 0, top: 0, width: 0, height: 20 }; };

// The picture: client coordinates are picture coordinates.
El.prototype.setPointerCapture = function (id) { this.captured = id; };
El.prototype.releasePointerCapture = function () { this.captured = null; };
El.prototype.getScreenCTM = function () { return { inverse: function () { return {}; } }; };
El.prototype.createSVGPoint = function () {
    return { x: 0, y: 0, matrixTransform: function () { return { x: this.x, y: this.y }; } };
};

// -- the document --------------------------------------------------------------
var document = new El('#document');
document.documentElement = document.appendChild(new El('html'));
document.head = document.documentElement.appendChild(new El('head'));
document.body = document.documentElement.appendChild(new El('body'));
document._focus = null;
Object.defineProperty(document, 'hidden', { value: false, writable: true });
document.createElement = function (tag) { return new El(tag); };
document.createElementNS = function (ns, tag) { return new El(tag, ns); };
document.createTextNode = function (s) { return new Text(s); };
document.getElementById = function (id) {
    var found = null;
    (function walk(node) {
        node.children.forEach(function (c) { if (!found && c.getAttribute('id') === id) found = c; if (!found) walk(c); });
    })(document);
    return found;
};
Object.defineProperty(document, 'activeElement', {
    get: function () { return document._focus && document._focus.isConnected ? document._focus : document.body; },
});

// Markup into elements: tags, attributes and text, no entities beyond what the templates use.
function parseHTML(html, parent) {
    var stack = [parent], re = /<!--[\s\S]*?-->|<\/([a-zA-Z0-9]+)\s*>|<([a-zA-Z0-9]+)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g, m;
    while ((m = re.exec(html))) {
        var top = stack[stack.length - 1];
        if (m[1]) {
            while (stack.length > 1 && stack.pop().tagName.toLowerCase() !== m[1].toLowerCase()) { /* close up to it */ }
        } else if (m[2]) {
            var tag = m[2].toLowerCase(), ns = tag === 'svg' || top.namespaceURI !== HTML_NS ? 'http://www.w3.org/2000/svg' : HTML_NS;
            var el = top.appendChild(new El(tag, ns)), attrRe = /([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g, a;
            while ((a = attrRe.exec(m[3]))) el.setAttribute(a[1], a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : '');
            if (!m[4] && !VOID[tag]) stack.push(el);
        } else if (m[5] && m[5].trim()) {
            top.appendChild(new Text(m[5].replace(/&amp;/g, '&')));
        }
    }
}

// -- timers and tasks, moved by hand ------------------------------------------
globalThis.setTimeout = function (fn) { timeouts.push(fn); return timeouts.length; };
globalThis.clearTimeout = function () {};
globalThis.setInterval = function (fn) { intervals.push(fn); return intervals.length; };
globalThis.clearInterval = function (id) { intervals[id - 1] = function () {}; };
function tickIntervals() { intervals.slice().forEach(function (fn) { fn(); }); }
var realSetImmediate = setImmediate;
// Lets every answer, promise and queued event run until nothing is left to do.
async function settle() {
    for (var i = 0; i < 40; i++) {
        await new Promise(function (r) { realSetImmediate(r); });
        while (tasks.length) tasks.shift()();
    }
}

// -- fetch: a station the test scripts ------------------------------------------
var requests = [];     // {method, url, body} of every request, in order
var held = [];         // answers held back by hold(), released by release()
var holdWhen = null;   // (request) -> true to hold its answer
var station = null;    // (request) -> {status, body, headers, redirected, unreadable} or 'unreachable'
function respond(status, body, headers) {
    var text = typeof body === 'string' ? body : JSON.stringify(body === undefined ? {} : body);
    return {
        ok: status >= 200 && status < 300,
        status: status,
        redirected: false,
        headers: { get: function (k) { return (headers || {})[k] === undefined ? null : headers[k]; } },
        json: function () { return Promise.resolve().then(function () { return JSON.parse(text); }); },
        text: function () { return Promise.resolve(text); },
        blob: function () { return Promise.resolve(new Blob([text])); },
    };
}
// A JSON body as its value, a form body as its text.
function readBody(body) { try { return JSON.parse(body); } catch (e) { return String(body); } }
globalThis.fetch = function (url, opts) {
    opts = opts || {};
    var req = { method: (opts.method || 'GET').toUpperCase(), url: url, body: opts.body ? readBody(opts.body) : undefined };
    requests.push(req);
    // The station answers when asked; a held answer only arrives later.
    var out = station(req);
    return new Promise(function (resolve, reject) {
        // An aborted request rejects at once and its answer, when it comes, is dropped.
        var aborted = false;
        if (opts.signal) {
            opts.signal.addEventListener('abort', function () {
                aborted = true;
                reject(new DOMException('The operation was aborted.', 'AbortError'));
            });
        }
        var answer = function () {
            if (aborted) return;
            if (out === 'unreachable') reject(new TypeError('Failed to fetch'));
            else {
                var res = respond(out.status || 200, out.body, out.headers);
                res.redirected = !!out.redirected;
                // The connection drops after the headers.
                if (out.unreadable) res.text = res.json = function () { return Promise.reject(new TypeError('network error')); };
                resolve(res);
            }
        };
        if (holdWhen && holdWhen(req)) held.push(answer);
        else Promise.resolve().then(answer);
    });
};
function release() { var all = held; held = []; all.forEach(function (a) { a(); }); }
function releaseFirst() { held.shift()(); }
function releaseLast() { held.pop()(); }

globalThis.window = globalThis;
globalThis.document = document;
