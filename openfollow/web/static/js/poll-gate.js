/*
 * OpenFollow poll gate.
 *
 * Every timed htmx poll costs the station a request thread, so a poll runs
 * only while someone can see what it refreshes:
 *
 *   hx-trigger="every 5s [pollShown(this)], poll-shown[pollShown(this)] from:body"
 *
 * ``pollShown(el)`` is false while the browser tab is hidden, while el's
 * section is folded, and while that section (or el, outside any section) is
 * not laid out: another tab, a hidden wizard step. It measures the section,
 * not el, so a ``hidden`` poll driver works too. A poll that must keep
 * running while its section is out of sight uses ``[!document.hidden]``.
 *
 * ``announcePollsShown()`` fires ``poll-shown`` on the body, so each poll
 * whose section just came into view refreshes at once instead of reading
 * stale until its next tick. It fires when the browser tab is shown again;
 * base.tpl calls it after a tab switch and an unfold.
 */
(function (root) {
    'use strict';

    function pollShown(el) {
        if (root.document.hidden) return false;
        var section = el.closest('.section');
        if (section && section.classList.contains('is-collapsed')) return false;
        return (section || el).getClientRects().length > 0;
    }

    function announcePollsShown() {
        if (root.htmx && root.document.body) root.htmx.trigger(root.document.body, 'poll-shown');
    }

    root.pollShown = pollShown;
    root.announcePollsShown = announcePollsShown;
    root.document.addEventListener('visibilitychange', function () {
        if (!root.document.hidden) announcePollsShown();
    });
})(window);
