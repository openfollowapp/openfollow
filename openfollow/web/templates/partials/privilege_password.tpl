%# Generic privilege-password modal. Renders empty when pending is None.
%# Polling div in base.tpl swaps this in every 3s so prompts refresh
%# without a page reload and disappear when pending clears.
% if pending:
<div class="update-notice awaiting-password" role="dialog" aria-modal="true"
     aria-labelledby="privilege-password-reason">
    <p id="privilege-password-reason">
        {{_('Device password required:')}} {{pending.get("reason") or pending.get("capability_name")}}
    </p>
    <label for="privilege-password-input" class="visually-hidden">{{_('Device password')}}</label>
    %# Plain <div> wrapper (not <form>) – the modal is injected into
    %# the global #privilege-password-modal container which lives
    %# outside any form, but we still suppress Enter's default to
    %# match the update-password modal's UX.
    <input id="privilege-password-input" type="password" name="password"
           placeholder="{{_('Device password')}}" autofocus autocomplete="off"
           aria-label="{{_('Device password')}}"
           hx-preserve="true"
           onkeydown="if(event.key==='Enter')event.preventDefault()"
           hx-post="/system/privilege/password"
           hx-trigger="keyup[key=='Enter']"
           hx-include="this"
           hx-target="#privilege-password-modal" hx-swap="innerHTML">
    <div class="actions">
        <button type="button" class="update-btn"
                hx-post="/system/privilege/password"
                hx-include="#privilege-password-input"
                hx-disabled-elt="this"
                hx-target="#privilege-password-modal" hx-swap="innerHTML">{{_('Submit')}}</button>
        <button type="button" class="secondary"
                hx-post="/system/privilege/password/cancel"
                hx-disabled-elt="this"
                hx-target="#privilege-password-modal" hx-swap="innerHTML">{{_('Cancel')}}</button>
    </div>
</div>
% end
