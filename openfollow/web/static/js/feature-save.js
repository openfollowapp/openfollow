/*
 * OpenFollow Features table: applied by the Station Settings Save.
 *
 * The table's boxes belong to the Save's form (``form=``), so nothing changes
 * until Save. A Save that switches a feature off asks first in the shared
 * modal, naming each such feature and what stops with it (``data-title``,
 * ``data-off``); Cancel sends nothing. Once a save has gone through, the page
 * flips its ``feature-<key>`` body classes and records the new stored state
 * (``data-saved``). That happens on ``htmx:beforeSwap``: the response replaces
 * the form, so ``htmx:afterRequest`` fires on a detached element and never
 * reaches ``document``.
 */
(function () {
    'use strict';
    var FORM_ID = 'general-network-section';

    function boxes() {
        return Array.prototype.slice.call(document.querySelectorAll('#features-section input[type="checkbox"]'));
    }

    function fromSave(evt) {
        return evt.detail && evt.detail.elt && evt.detail.elt.id === FORM_ID;
    }

    document.addEventListener('htmx:confirm', function (evt) {
        if (!fromSave(evt)) return;
        var off = boxes().filter(function (cb) { return cb.dataset.saved === '1' && !cb.checked; });
        if (!off.length) return;
        evt.preventDefault();
        var items = off.map(function (cb) {
            return '<li><strong>' + escapeHTML(cb.dataset.title) + '</strong>: ' + escapeHTML(cb.dataset.off) + '</li>';
        }).join('');
        openModal({
            title: off.length === 1 ? 'Turn this feature off?' : 'Turn these features off?',
            bodyHTML: '<p>Saving switches ' + (off.length === 1 ? 'it' : 'them') + ' off on this station now.</p>'
                + '<ul class="feature-off-list">' + items + '</ul>',
            footerButtons: [
                { label: 'Cancel', onClick: function () { closeModal(); } },
                { label: 'Turn off', kind: 'danger', onClick: function () { closeModal(); evt.detail.issueRequest(true); } },
            ],
        });
    });

    document.addEventListener('htmx:beforeSwap', function (evt) {
        if (!fromSave(evt) || evt.detail.isError) return;
        boxes().forEach(function (cb) {
            document.body.classList.toggle('feature-' + cb.getAttribute('name'), cb.checked);
            cb.dataset.saved = cb.checked ? '1' : '0';
        });
    });
})();
