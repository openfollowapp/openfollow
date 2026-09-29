/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Copyright (C) 2026 OpenFollow Project
 *
 * Applies the validate route's verdict on a binding edit (the ``bindingChecked``
 * event): the field that lost its input is emptied and marked, and a note that
 * named a field which has since let go of the input stops naming it. The
 * server decides what moves; this file only shows it.
 */
(function () {
  'use strict';

  function control(form, name) {
    return form.querySelector('[name="' + CSS.escape(name) + '"]');
  }

  // A "moved to X" note no longer holds once X changes: keep the red, drop the name.
  function forget(form, name) {
    form.querySelectorAll('[data-moved-to="' + CSS.escape(name) + '"]').forEach(function (note) {
      note.textContent = note.getAttribute('data-lost-text');
      note.removeAttribute('data-moved-to');
    });
  }

  document.addEventListener('bindingChecked', function (e) {
    var d = e.detail || {};
    var form = e.target && e.target.closest ? e.target.closest('form') : null;
    if (!form || !d.field) return;
    var own = control(form, d.field);
    if (own) own.removeAttribute('data-binding-lost');
    forget(form, d.field);
    (d.moved || []).forEach(function (m) {
      var el = control(form, m.field);
      if (!el) return;
      el.value = m.unbound;
      el.setAttribute('data-binding-lost', '');
      var span = document.getElementById(el.getAttribute('aria-describedby'));
      if (span) span.innerHTML = m.html;
      forget(form, m.field);
    });
    (d.recheck || []).forEach(function (name) {
      var el = control(form, name);
      if (el && window.htmx) window.htmx.trigger(el, 'change');
    });
  });

  // An OSC trigger on an input an action also uses carries the caution outline.
  function markOverlap(e) {
    var target = e.detail && e.detail.target;
    if (!target || !target.classList || !target.classList.contains('field-error')) return;
    var input = document.querySelector('[aria-describedby="' + CSS.escape(target.id) + '"]');
    if (input) input.toggleAttribute('data-binding-overlap', target.querySelector('.field-caution-msg') !== null);
  }
  document.addEventListener('htmx:afterSwap', markOverlap);

  // Reset to Defaults sets values without an event, so every note it leaves is stale.
  window.OpenFollow = window.OpenFollow || {};
  window.OpenFollow.clearBindingNotes = function (container) {
    container.querySelectorAll('[data-binding-lost]').forEach(function (el) {
      el.removeAttribute('data-binding-lost');
    });
    container.querySelectorAll('[aria-describedby]').forEach(function (el) {
      var span = document.getElementById(el.getAttribute('aria-describedby'));
      if (span && span.classList.contains('field-error')) span.innerHTML = '';
      el.setAttribute('aria-invalid', 'false');
    });
  };
})();
