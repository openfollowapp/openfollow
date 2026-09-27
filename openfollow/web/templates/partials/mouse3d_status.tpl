%# Only what keeps a 3D Mouse from working; a working puck is listed under
%# Controller Slots. The poll answers 204 while the key matches, so a box is
%# inserted, and announced, once per change. It targets itself: the section's
%# form targets the whole section, and htmx would hand that down to the poll.
% from openfollow.input.mouse3d_status import none_connected, status_key, status_notices
<div id="mouse3d-status"
     hx-get="/section/mouse3d/status?key={{status_key(mouse3d_status)}}"
     hx-trigger="every 1s [!this.closest('.section').classList.contains('is-collapsed') && this.closest('.tab-content').classList.contains('active')]"
     hx-target="this" hx-swap="outerHTML">
% for n in status_notices(mouse3d_status):
% if n.level == 'error':
    <div class="notice error" role="alert" aria-live="assertive" aria-atomic="true">
% else:
    <div class="notice" role="status" aria-live="polite" aria-atomic="true">
% end
        <div>{{n.text}}</div>
% if n.step:
        <div class="notice-sub">{{n.step}}</div>
% end
    </div>
% end
% if none_connected(mouse3d_status):
    <p class="m3d-empty">No 3D Mouse connected.</p>
% end
</div>
