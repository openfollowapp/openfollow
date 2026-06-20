/*
 * OpenFollow color picker.
 *
 * A circle-swatch picker that replaces every native <input type="color"> in the
 * web UI. The palette is seeded by base.tpl into ``window.OPENFOLLOW_PALETTE``
 * so the picker reads the same hexes + names + auto-pick order as the Python
 * seeders (openfollow.palette.AUTO_PICK_ORDER).
 *
 * Two modes:
 *   - 'full'  – 5 hue columns × 4 brightness rows + grey row (25 swatches).
 *               Used for the marker catalog and zone editor row pickers.
 *   - 'greys' – grey row only (5 swatches). Used for the crosshair colour and
 *               grid line colour fields, which are always meant to be neutral.
 * Both modes include a free-form hex input + preview circle so any non-palette
 * colour is still reachable.
 *
 * Exposes two helpers on ``window.OpenFollow``:
 *   - attachColorPicker(triggerEl, { mode, value, onChange })
 *       Wires a button to open the popover; updates dataset.value + bg colour
 *       on commit; calls onChange(hex).
 *   - nextUnusedColor(usedHexes)
 *       Walks AUTO_PICK_ORDER case-insensitively, returns the first hex not in
 *       ``usedHexes``. Mirrors openfollow.palette.next_unused_color so the
 *       JS-driven catalog and zone editors pick the same default as the Python
 *       seeders.
 *
 * Server-rendered triggers (``<button class="color-swatch-trigger"
 * data-color-picker="full|greys">``) are auto-initialised on DOMContentLoaded;
 * JS-rendered triggers (marker catalog rows / zone editor rows) call
 * attachColorPicker explicitly inside their row-creation paths.
 */
(function () {
    'use strict';

    // Stub the public API for graceful degradation if palette is missing.
    // Real implementations overwrite these at the bottom of the IIFE.
    window.OpenFollow = window.OpenFollow || {};
    if (typeof window.OpenFollow.attachColorPicker !== 'function') {
        window.OpenFollow.attachColorPicker = function () {};
    }
    if (typeof window.OpenFollow.nextUnusedColor !== 'function') {
        window.OpenFollow.nextUnusedColor = function () { return '#000000'; };
    }
    if (typeof window.OpenFollow.closeColorPicker !== 'function') {
        window.OpenFollow.closeColorPicker = function () {};
    }

    var PALETTE = window.OPENFOLLOW_PALETTE;
    if (!PALETTE || !Array.isArray(PALETTE.hue_columns) || !Array.isArray(PALETTE.greys)) {
        // No palette injected – defer (script may have loaded before the seed).
        // Fail loud in the console so a missing base.tpl injection is obvious.
        console.error('color-picker: window.OPENFOLLOW_PALETTE missing or malformed');
        return;
    }

    function normHex(hex) {
        return String(hex || '').trim().toUpperCase();
    }

    function isValidHex6(s) {
        return /^#?[0-9A-Fa-f]{6}$/.test(s);
    }

    function toCanonicalHex(s) {
        var v = String(s || '').trim();
        if (v.charAt(0) !== '#') v = '#' + v;
        return v.toUpperCase();
    }

    function nextUnusedColor(usedHexes) {
        var used = {};
        (usedHexes || []).forEach(function (h) {
            // Skip blanks AFTER normalising (a "   " normalises to ""), matching
            // Python next_unused_color, so the all-used fallback index agrees.
            var n = normHex(h);
            if (n) used[n] = true;
        });
        for (var i = 0; i < PALETTE.auto_pick_order.length; i++) {
            var hex = PALETTE.auto_pick_order[i];
            if (!used[normHex(hex)]) return hex;
        }
        return PALETTE.auto_pick_order[Object.keys(used).length % PALETTE.auto_pick_order.length];
    }

    // ---- Singleton popover state. Only one picker is open at a time.
    var active = null;

    function closeActive() {
        if (!active) return;
        active.cleanup();
        active.popover.remove();
        active = null;
    }

    function makeSwatch(entry, currentValue) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'color-picker-swatch';
        btn.dataset.hex = entry.hex;
        btn.title = entry.name + ' – ' + entry.hex;
        btn.setAttribute('aria-label', entry.name);
        btn.style.background = entry.hex;
        if (currentValue && normHex(currentValue) === normHex(entry.hex)) {
            btn.classList.add('selected');
        }
        return btn;
    }

    function buildPopover(trigger, mode, initialValue, onPick) {
        var popover = document.createElement('div');
        popover.className = 'color-picker-popover';
        popover.setAttribute('role', 'dialog');

        // Build swatches: full = hue grid + greys; greys = greys only.
        var swatches = [];

        if (mode === 'full') {
            var grid = document.createElement('div');
            grid.className = 'color-picker-grid';
            for (var row = 0; row < 4; row++) {
                for (var col = 0; col < PALETTE.hue_columns.length; col++) {
                    var column = PALETTE.hue_columns[col];
                    if (!column[row]) continue;
                    var sw = makeSwatch(column[row], initialValue);
                    grid.appendChild(sw);
                    swatches.push(sw);
                }
            }
            popover.appendChild(grid);
        }

        var greysRow = document.createElement('div');
        greysRow.className = 'color-picker-greys';
        for (var gi = 0; gi < PALETTE.greys.length; gi++) {
            var gsw = makeSwatch(PALETTE.greys[gi], initialValue);
            greysRow.appendChild(gsw);
            swatches.push(gsw);
        }
        popover.appendChild(greysRow);

        // Hex input with preview circle.
        var hexRow = document.createElement('div');
        hexRow.className = 'color-picker-hex';
        var hash = document.createElement('span');
        hash.className = 'color-picker-hash';
        hash.textContent = '#';
        var hexInput = document.createElement('input');
        hexInput.type = 'text';
        hexInput.pattern = '^#?[0-9A-Fa-f]{6}$';
        hexInput.maxLength = 7;
        hexInput.spellcheck = false;
        hexInput.autocomplete = 'off';
        hexInput.setAttribute('aria-label', 'Hex color');
        hexInput.value = String(initialValue || '').replace(/^#/, '');
        var preview = document.createElement('span');
        preview.className = 'color-picker-preview';
        preview.style.background = initialValue || '#000000';
        hexRow.appendChild(hash);
        hexRow.appendChild(hexInput);
        hexRow.appendChild(preview);
        popover.appendChild(hexRow);

        // Commit on swatch click or valid hex input.
        function commit(hex) {
            var canon = toCanonicalHex(hex);
            preview.style.background = canon;
            // Keep the hex input in sync (without leading #).
            if (hexInput.value.replace(/^#/, '').toUpperCase() !== canon.slice(1)) {
                hexInput.value = canon.slice(1);
            }
            swatches.forEach(function (sw) {
                sw.classList.toggle('selected', normHex(sw.dataset.hex) === normHex(canon));
            });
            onPick(canon);
        }

        swatches.forEach(function (sw) {
            sw.addEventListener('click', function (e) {
                e.stopPropagation();
                commit(sw.dataset.hex);
            });
        });

        hexInput.addEventListener('input', function () {
            var v = hexInput.value.trim();
            if (v && v.charAt(0) !== '#') v = '#' + v;
            if (isValidHex6(v)) commit(v);
        });
        hexInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                closeActive();
            }
        });

        // Keyboard navigation: arrows move focus, Enter commits, Esc closes.
        swatches.forEach(function (sw, idx) {
            sw.addEventListener('keydown', function (e) {
                var cols = (mode === 'full') ? PALETTE.hue_columns.length : PALETTE.greys.length;
                if (e.key === 'ArrowRight') {
                    e.preventDefault();
                    var nxt = swatches[(idx + 1) % swatches.length];
                    nxt && nxt.focus();
                } else if (e.key === 'ArrowLeft') {
                    e.preventDefault();
                    var prev = swatches[(idx - 1 + swatches.length) % swatches.length];
                    prev && prev.focus();
                } else if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    var down = swatches[idx + cols];
                    if (down) down.focus();
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    var up = swatches[idx - cols];
                    if (up) up.focus();
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    commit(sw.dataset.hex);
                    closeActive();
                }
            });
        });

        // Position below trigger, clamped to viewport right.
        document.body.appendChild(popover);
        var rect = trigger.getBoundingClientRect();
        popover.style.position = 'absolute';
        popover.style.top = (rect.bottom + window.scrollY + 4) + 'px';
        var maxLeft = window.scrollX + window.innerWidth - popover.offsetWidth - 8;
        popover.style.left = Math.max(8, Math.min(rect.left + window.scrollX, maxLeft)) + 'px';

        // Close on outside click or Esc.
        var onDocClick = function (e) {
            if (popover.contains(e.target) || e.target === trigger) return;
            closeActive();
        };
        var onKey = function (e) {
            if (e.key === 'Escape') { closeActive(); trigger.focus(); }
        };
        // Defer attach to prevent immediate close on opening click.
        setTimeout(function () {
            document.addEventListener('click', onDocClick);
            document.addEventListener('keydown', onKey);
        }, 0);

        return {
            popover: popover,
            trigger: trigger,
            cleanup: function () {
                document.removeEventListener('click', onDocClick);
                document.removeEventListener('keydown', onKey);
            },
        };
    }

    function attachColorPicker(triggerEl, opts) {
        opts = opts || {};
        var mode = (opts.mode === 'greys') ? 'greys' : 'full';
        var value = opts.value || '#000000';
        var onChange = opts.onChange || function () {};

        // Always refresh visible state so callers can re-pass new values.
        // Guard against stacking click handlers on persistent nodes.
        triggerEl.classList.add('color-swatch-trigger');
        triggerEl.style.background = value;
        triggerEl.dataset.value = value;
        triggerEl.dataset.colorPicker = mode;
        if (!triggerEl.getAttribute('type')) triggerEl.setAttribute('type', 'button');
        if (!triggerEl.getAttribute('aria-label')) {
            triggerEl.setAttribute('aria-label', 'Pick color');
        }
        if (triggerEl.dataset.pickerAttached === 'true') {
            return;
        }
        triggerEl.dataset.pickerAttached = 'true';

        triggerEl.addEventListener('click', function (e) {
            e.stopPropagation();
            if (active && active.trigger === triggerEl) {
                closeActive();
                return;
            }
            closeActive();
            active = buildPopover(triggerEl, mode, triggerEl.dataset.value, function (hex) {
                triggerEl.style.background = hex;
                triggerEl.dataset.value = hex;
                onChange(hex);
            });
        });
    }

    // Auto-init server-rendered triggers; JS-rendered partials skip this.
    function initServerRendered() {
        var triggers = document.querySelectorAll(
            'button.color-swatch-trigger[data-color-picker]'
        );
        triggers.forEach(function (trigger) {
            if (trigger.dataset.pickerAttached === 'true') return;
            var mode = trigger.dataset.colorPicker;
            // Hidden <input> sibling carries form value, seeds the picker.
            var hidden = trigger.nextElementSibling;
            var value = trigger.dataset.value ||
                (hidden && hidden.tagName === 'INPUT' ? hidden.value : '') ||
                '#000000';
            attachColorPicker(trigger, {
                mode: mode,
                value: value,
                onChange: function (hex) {
                    if (hidden && hidden.tagName === 'INPUT') {
                        hidden.value = hex;
                        hidden.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                },
            });
            trigger.dataset.pickerAttached = 'true';
        });
    }

    // Expose the API before DOM-deferred wiring runs in <body>.
    window.OpenFollow = window.OpenFollow || {};
    window.OpenFollow.attachColorPicker = attachColorPicker;
    window.OpenFollow.nextUnusedColor = nextUnusedColor;
    window.OpenFollow.closeColorPicker = closeActive;

    // Defer DOM wiring until body exists.
    function wireDom() {
        initServerRendered();
        // Re-scan after htmx swaps for server-rendered partials.
        document.body.addEventListener('htmx:afterSwap', initServerRendered);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wireDom);
    } else {
        wireDom();
    }
})();
