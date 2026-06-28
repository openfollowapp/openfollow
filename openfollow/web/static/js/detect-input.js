/*
 * OpenFollow detect-input widget.
 *
 * A reusable "press to capture" control: a button paired with an input that
 * polls a server endpoint for a detected value and writes it into the field.
 * Used by the 3D Mouse button binds (click Detect, then press the device
 * button), and available to any field that wants press-to-capture.
 *
 * Server-rendered markup (event-delegated on ``document`` so HTMX-swapped
 * sections keep working without re-init):
 *
 *   <div class="detect-input">
 *     <input id="my-field" ...>
 *     <button type="button" class="detect-btn"
 *             data-detect-input="my-field"
 *             data-detect-url="/section/.../detect">Detect</button>
 *   </div>
 *
 * The endpoint returns JSON ``{"value": <number|null>}`` (``"button"`` is also
 * accepted). A number is written into the field; ``null`` (nothing detected)
 * leaves the field unchanged. After a write a synthetic input/change/blur fires
 * so the field's ``blur changed`` HTMX validation and the form's Save gate
 * refresh.
 */
(function () {
    'use strict';

    var LISTENING = 'Listening…';

    function targetInput(btn) {
        var id = btn.getAttribute('data-detect-input');
        return id ? document.getElementById(id) : null;
    }

    function writeValue(input, value) {
        input.value = value === null || value === undefined ? '' : String(value);
        // Programmatic mutations don't fire these on their own; dispatch so the
        // dirty-state gate and the field's ``blur changed`` validation run.
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
    }

    document.addEventListener('click', function (event) {
        var btn = event.target.closest('[data-detect-input]');
        if (!btn || btn.disabled) {
            return;
        }
        var input = targetInput(btn);
        var url = btn.getAttribute('data-detect-url');
        if (!input || !url) {
            return;
        }
        event.preventDefault();

        var original = btn.textContent;
        btn.disabled = true;
        btn.textContent = LISTENING;

        fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' })
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (data) {
                if (data && typeof data === 'object') {
                    var value = 'value' in data ? data.value : data.button;
                    if (value !== undefined && value !== null) {
                        writeValue(input, value);
                    }
                }
            })
            .catch(function () { /* leave the field unchanged on error */ })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = original;
            });
    });
})();
